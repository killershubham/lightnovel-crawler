# -*- coding: utf-8 -*-
import logging
from typing import List

from lncrawl.core.crawler import Crawler
from lncrawl.models import Chapter, SearchResult

logger = logging.getLogger(__name__)

class LiteroticaCrawler(Crawler):
    base_url = ["https://www.literotica.com/"]

    def initialize(self) -> None:
        self.init_executor(ratelimit=2)

    def search_novel(self, query) -> List[SearchResult]:
        soup = self.get_soup(
            f"https://search.literotica.com/?query={query}", timeout=50
        )
        results = []
        for item in soup.select("div.panel.ai_gJ > div.ai_iG > a.ai_ii"):
            results.append(SearchResult(title=item.text.strip(), url=item["href"]))
        return results

    def read_novel_info(self) -> None:
        if "/series/" in self.novel_url:
            raise Exception("Literotica series pages are blocked by Javascript! Please use a Chapter URL (e.g., /s/wicked-hormones-ch-01) instead.")

        soup = self.get_soup(self.novel_url, timeout=50)

        def extract_nav_link(target_soup, target_title):
            """Safely extracts the Next/Prev link, ignoring React HTML comments."""
            icon = target_soup.select_one(f"i[title='{target_title}']")
            if icon:
                container = icon.find_parent("div")
                if container:
                    a_tag = container.select_one("a[href*='/s/']")
                    if a_tag and a_tag.has_attr("href"):
                        href = a_tag["href"].split("?")[0] # Strip pagination queries
                        return href if href.startswith("http") else "https://www.literotica.com" + href
            return None

        def get_nav_link(current_soup, target_title, base_chapter_url):
            """Finds the nav link, jumping to the last page of the chapter if necessary."""
            # 1. Check current page
            link = extract_nav_link(current_soup, target_title)
            if link: return link
            
            # 2. Not found? Find the max page of this chapter and jump to it
            max_page = 1
            page_input = current_soup.select_one("input[name='page']")
            if page_input and page_input.has_attr("max"):
                try: max_page = int(page_input["max"])
                except: pass
                
            if max_page == 1:
                # Fallback: scan pagination spans/links
                for item in current_soup.select("a[class*='_pagination__item'], span[class*='_pagination__item']"):
                    try:
                        num = int(item.text.strip())
                        if num > max_page: max_page = num
                    except: pass
            
            # 3. If there are multiple pages, fetch the last one to find the nav link
            if max_page > 1:
                last_page_url = f"{base_chapter_url}?page={max_page}"
                last_soup = self.get_soup(last_page_url, timeout=50)
                return extract_nav_link(last_soup, target_title)
                
            return None

        # 1. Traverse BACKWARDS
        print("⏪ Traversing backwards to find Chapter 1...", flush=True)
        current_url = self.novel_url
        current_soup = soup
        
        prev_url = get_nav_link(current_soup, "Previous Part", current_url)
        while prev_url and prev_url != current_url:
            print(f"   Found Previous: {prev_url}", flush=True)
            current_url = prev_url
            current_soup = self.get_soup(current_url, timeout=50)
            prev_url = get_nav_link(current_soup, "Previous Part", current_url)

        # 2. Extract Master Metadata
        series_link = current_soup.select_one("a[href*='/series/se/']")
        if series_link and series_link.text:
            self.novel_title = series_link.text.strip()
        else:
            title_tag = current_soup.select_one("h1.headline, h1.j_bm, h1[class*='_title_'], h1")
            self.novel_title = title_tag.text.strip() if title_tag else "Unknown Title"
            
        author_tag = current_soup.select_one("a.y_eU, a[class*='_author__title']")
        self.novel_author = author_tag.text.strip() if author_tag else "Unknown Author"
        
        cover_tag = current_soup.select_one("a.y_eR > img, img[class*='_cover_']")
        self.novel_cover = cover_tag["src"] if cover_tag else None
        
        all_series_tags = set()
        master_synopsis = "Series Synopsis:\n"

        # 3. Traverse FORWARDS
        print("⏩ Traversing forwards to collect all chapters...", flush=True)
        while current_url:
            chap_title_tag = current_soup.select_one("h1.headline, h1.j_bm, h1[class*='_title_'], h1")
            chap_title = chap_title_tag.text.strip() if chap_title_tag else f"Chapter {len(self.chapters) + 1}"

            synopsis_candidates = current_soup.select("div.bn_B, p.j_bq, div[class*='_widget__info_']")
            chap_synopsis = "(No standalone synopsis found)"
            for cand in synopsis_candidates:
                if cand.text and "part of the" not in cand.text.lower():
                    chap_synopsis = cand.text.strip()
                    break
            
            chap_tags_nodes = current_soup.select('a.av_as, a[href^="https://tags.literotica.com/"]')
            chap_tags = [item.text.strip() for item in chap_tags_nodes]
            all_series_tags.update(chap_tags)

            tags_display = f" [Tags: {', '.join(chap_tags)}]" if chap_tags else ""
            master_synopsis += f"\n{chap_title}{tags_display}:\n{chap_synopsis}\n"

            self.chapters.append(
                dict(id=len(self.chapters) + 1, title=chap_title, url=current_url)
            )

            next_url = get_nav_link(current_soup, "Next Part", current_url)
            if next_url and next_url not in [c["url"] for c in self.chapters]:
                print(f"   Found Next: {next_url}", flush=True)
                current_url = next_url
                current_soup = self.get_soup(current_url, timeout=50)
            else:
                break
                
        self.novel_synopsis = master_synopsis.strip()
        self.novel_tags = list(all_series_tags)
        print(f"✅ Found {len(self.chapters)} total chapters.", flush=True)

    def download_chapter_body(self, chapter: Chapter) -> str:
        soup = self.get_soup(chapter['url'], timeout=50)
        chapterText = ""
        current_page = 1
        
        while 1:
            print(f"   Downloading {chapter['title']} - Page {current_page}...", flush=True)
            
            content_div = soup.select_one('div[itemprop="articleBody"]')
            if not content_div:
                content_div = soup.select_one("div[class*='_article__content_'], div.aa_ht")

            if not content_div:
                containers = soup.select("div")
                best_container = None
                max_p = 0
                for c in containers:
                    p_count = len(c.find_all("p", recursive=False))
                    if p_count > max_p:
                        max_p = p_count
                        best_container = c
                if best_container and max_p >= 2: 
                    content_div = best_container

            if content_div:
                chapterText += self.cleaner.extract_contents(content_div)
            else:
                print(f"   ⚠️ Could not locate story body on page {current_page}", flush=True)
            
            try:
                next_page_tag = soup.select_one("a[title='Next Page'], a[class*='_pagination__item--next']")
                
                if not next_page_tag or next_page_tag.name != 'a' or not next_page_tag.has_attr("href"):
                    break
                
                nextUrl = next_page_tag["href"]
                if not nextUrl.startswith("http"):
                    nextUrl = "https://www.literotica.com" + nextUrl
                
                soup = self.get_soup(nextUrl, timeout=100)
                current_page += 1
            except Exception as e:
                print(f"   ⚠️ Pagination error: {e}", flush=True)
                break
                
        return chapterText.replace('"/images/', '"https://www.literotica.com/images/')
