import os
import time
import json
import subprocess
import requests
import logging
import yaml

from bs4 import BeautifulSoup
from markdownify import markdownify as md

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager


# ========================
# 日志配置
# ========================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S"
)

logger = logging.getLogger(__name__)


# ========================
# 配置区
# ========================

OUTPUT_DIR = "markdown"  # 源文件目录
IMAGE_DIR = os.path.join(OUTPUT_DIR, "images")
CONFIG_FILE = "articles.txt"
DOWNLOADED_FILE = "downloaded.json"  # 记录已下载的URL


def load_downloaded_urls():
    """加载已下载的URL列表"""
    if not os.path.exists(DOWNLOADED_FILE):
        return set()
    
    try:
        with open(DOWNLOADED_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return set(data.get("urls", []))
    except Exception as e:
        logger.warning(f"读取已下载记录失败: {e}")
        return set()


def save_downloaded_url(url):
    """保存已下载的URL"""
    downloaded = load_downloaded_urls()
    downloaded.add(url)
    
    try:
        with open(DOWNLOADED_FILE, "w", encoding="utf-8") as f:
            json.dump({"urls": list(downloaded)}, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"保存下载记录失败: {e}")


def load_articles():
    """从配置文件加载文章URL列表"""
    if not os.path.exists(CONFIG_FILE):
        logger.error(f"配置文件 {CONFIG_FILE} 不存在")
        return []
    
    try:
        urls = []
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    urls.append(line)
        
        logger.info(f"成功加载 {len(urls)} 个文章URL")
        return urls
    except Exception as e:
        logger.error(f"读取配置文件出错: {e}")
        return []


# ========================


def init_driver():
    logger.info("初始化 Chrome 浏览器...")
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--disable-gpu")
    options.add_argument("--lang=zh-CN")
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
    )

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)

    logger.info("ChromeDriver 初始化完成")
    return driver


def fetch_article_html(driver, url):
    logger.info(f"打开文章页面: {url}")
    driver.get(url)
    time.sleep(4)

    soup = BeautifulSoup(driver.page_source, "html.parser")

    # 提取文章标题
    title = None
    title_elem = soup.find("h1", id="js_title")
    if title_elem:
        title = title_elem.get_text(strip=True)
    
    if not title:
        title_tag = soup.find("title")
        if title_tag:
            title = title_tag.get_text(strip=True).replace(" - 微信公众平台", "").strip()
    
    if not title:
        title = "未知标题"
    
    logger.info(f"文章标题: {title}")

    content = soup.find("div", id="js_content")
    if content is None:
        logger.warning("未找到 js_content，尝试 rich_media_content")
        content = soup.find("div", class_="rich_media_content")

    if content is None:
        logger.error("文章内容节点未找到")
        raise Exception("无法找到文章内容")

    logger.info("成功解析文章主体内容")
    return title, content


def download_image(url, filename):
    logger.info(f"下载图片: {url}")
    r = requests.get(url, timeout=15)
    with open(filename, "wb") as f:
        f.write(r.content)


def process_images(html):
    logger.info("处理文章中的图片...")
    os.makedirs(IMAGE_DIR, exist_ok=True)

    soup = BeautifulSoup(str(html), "html.parser")
    imgs = soup.find_all("img")

    logger.info(f"发现 {len(imgs)} 张图片")

    for i, img in enumerate(imgs):
        src = img.get("data-src") or img.get("src")
        if not src:
            continue

        ext = ".jpg"
        if "png" in src:
            ext = ".png"

        filename = f"img_{i}{ext}"
        filepath = os.path.join(IMAGE_DIR, filename)

        try:
            download_image(src, filepath)
            img["src"] = f"images/{filename}"
            logger.info(f"图片保存成功: {filename}")
        except Exception as e:
            logger.error(f"图片下载失败: {src} | {e}")

    return str(soup)


def save_markdown(title, html):
    logger.info("转换为 Markdown 格式...")
    
    # 简单转换为 markdown
    md_text = md(html)
    
    # 清理多余的空行（但保留段落间距）
    lines = md_text.split('\n')
    cleaned_lines = []
    prev_empty = False
    
    for line in lines:
        if line.strip():  # 非空行
            cleaned_lines.append(line)
            prev_empty = False
        elif not prev_empty:  # 只保留一个空行
            cleaned_lines.append('')
            prev_empty = True
    
    # 移除末尾多余空行
    while cleaned_lines and not cleaned_lines[-1].strip():
        cleaned_lines.pop()
    
    md_text = '\n'.join(cleaned_lines)
    md_text = f"# {title}\n\n" + md_text

    # 清理文件名中的非法字符
    safe_title = "".join(c if c.isalnum() or c in "._- " else "" for c in title).strip()
    safe_title = safe_title.replace(" ", "_") or "article"
    filepath = os.path.join(OUTPUT_DIR, f"{safe_title}.md")

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(md_text)

    logger.info(f"Markdown 文件已生成: {filepath}")
    return f"{safe_title}.md"


def update_mkdocs_nav(articles_files):
    """更新 mkdocs.yml 导航，添加新文章"""
    mkdocs_file = "mkdocs.yml"
    
    try:
        with open(mkdocs_file, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
        
        # 初始化 nav
        if "nav" not in config:
            config["nav"] = []
        
        # 获取现有的导航项
        existing_files = set()
        new_nav = []
        
        for item in config["nav"]:
            if isinstance(item, dict):
                for title, path in item.items():
                    existing_files.add(path)
                    new_nav.append(item)
            else:
                new_nav.append(item)
        
        # 添加新文章到导航
        for article_file in articles_files:
            if article_file not in existing_files:
                # 从文件名提取标题（去掉 .md 和下划线转空格）
                title = article_file.replace(".md", "").replace("_", " ")
                new_nav.append({title: article_file})
                logger.info(f"将 '{title}' 添加到导航")
        
        config["nav"] = new_nav
        
        # 保存更新后的配置
        with open(mkdocs_file, "w", encoding="utf-8") as f:
            yaml.dump(config, f, allow_unicode=True, default_flow_style=False)
        
        logger.info("mkdocs.yml 已更新")
    except Exception as e:
        logger.error(f"更新 mkdocs.yml 失败: {e}")


def build_mkdocs():
    """运行 mkdocs build 重新构建网站"""
    try:
        logger.info("开始构建 MkDocs 网站...")
        result = subprocess.run(["python", "-m", "mkdocs", "build"], capture_output=True, text=True)
        
        if result.returncode == 0:
            logger.info("MkDocs 网站构建成功 ✅")
        else:
            logger.error(f"MkDocs 构建失败: {result.stderr}")
    except Exception as e:
        logger.error(f"运行 mkdocs build 出错: {e}")


def main():
    logger.info("===== 程序启动 =====")
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    urls = load_articles()
    if not urls:
        logger.warning("没有文章需要处理")
        return

    # 加载已下载的URL
    downloaded_urls = load_downloaded_urls()
    logger.info(f"已下载记录: {len(downloaded_urls)} 篇文章")
    
    # 过滤出未下载的URL
    urls_to_process = [url for url in urls if url not in downloaded_urls]
    
    if not urls_to_process:
        logger.info("所有文章都已下载，无需处理")
        return
    
    logger.info(f"需要处理: {len(urls_to_process)} 篇新文章")

    driver = init_driver()
    articles_files = []

    for url in urls_to_process:
        try:
            title, html = fetch_article_html(driver, url)
            logger.info(f"开始处理文章: {title}")
            html = process_images(html)
            md_file = save_markdown(title, html)
            articles_files.append(md_file)
            
            # 保存已下载的URL
            save_downloaded_url(url)
            logger.info(f"文章完成: {title}")
        except Exception as e:
            logger.error(f"文章处理失败: {url} | {e}")

    driver.quit()
    logger.info("浏览器已关闭")
    
    # 更新 mkdocs 配置和构建网站
    if articles_files:
        logger.info("正在更新 MkDocs 配置...")
        update_mkdocs_nav(articles_files)
        build_mkdocs()
    
    logger.info("===== 全部任务完成 ✅ =====")


if __name__ == "__main__":
    main()
