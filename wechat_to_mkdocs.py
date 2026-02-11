import os
import time
import json
import subprocess
import requests
import logging
import yaml
import hashlib
import shutil

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
    """加载已下载的URL列表（返回字典：URL -> 文件信息）"""
    if not os.path.exists(DOWNLOADED_FILE):
        return {}
    
    try:
        with open(DOWNLOADED_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            # 兼容旧格式（仅 URL 列表）
            if isinstance(data.get("urls"), list):
                return {url: {"filename": None} for url in data.get("urls", [])}
            return data.get("articles", {})
    except Exception as e:
        logger.warning(f"读取已下载记录失败: {e}")
        return {}


def save_downloaded_url(url, filename):
    """保存已下载的URL及其文件信息"""
    articles = load_downloaded_urls()
    articles[url] = {
        "filename": filename,
        "downloaded_at": time.strftime("%Y-%m-%d %H:%M:%S")
    }
    
    try:
        with open(DOWNLOADED_FILE, "w", encoding="utf-8") as f:
            json.dump({"articles": articles}, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"保存下载记录失败: {e}")


def load_delete_urls():
    """加载要删除的 URL 列表"""
    if not os.path.exists(CONFIG_FILE):
        return []
    
    try:
        delete_urls = []
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("#DELETE:"):
                    url = line.replace("#DELETE:", "").strip()
                    if url:
                        delete_urls.append(url)
        
        if delete_urls:
            logger.info(f"发现 {len(delete_urls)} 篇要删除的文章")
        return delete_urls
    except Exception as e:
        logger.error(f"读取删除列表失败: {e}")
        return []


def delete_article(url):
    """删除指定 URL 对应的文章文件和图片"""
    articles = load_downloaded_urls()
    
    if url not in articles:
        logger.warning(f"未找到该 URL 的下载记录: {url}")
        return False
    
    filename = articles[url].get("filename")
    logger.info(f"开始删除文章，记录的文件名: {filename}")
    
    # 如果没有记录文件名，尝试从 markdown 目录中查找
    if not filename:
        logger.info("📍 未记录文件名，尝试自动查找...")
        if os.path.exists(OUTPUT_DIR):
            for f in os.listdir(OUTPUT_DIR):
                if f.endswith('.md'):
                    filepath = os.path.join(OUTPUT_DIR, f)
                    try:
                        with open(filepath, 'r', encoding='utf-8') as file:
                            content = file.read()
                            # 检查文件是否包含该 URL（通常在文章中会出现）
                            if url in content:
                                filename = f
                                logger.info(f"✅ 找到匹配的文件: {f}")
                                break
                    except:
                        pass
    
    # 删除 markdown 文件
    deleted = False
    if filename:
        filepath = os.path.join(OUTPUT_DIR, filename)
        logger.info(f"准备删除: {filepath}")
        if os.path.exists(filepath):
            try:
                os.remove(filepath)
                logger.info(f"✅ 已删除文章文件: {filepath}")
                deleted = True
            except Exception as e:
                logger.error(f"❌ 删除文件失败: {filepath} | {e}")
        else:
            logger.warning(f"⚠️  文件不存在: {filepath}")
    else:
        logger.warning(f"⚠️  未能确定文件名，跳过文章文件删除")
    
    # 删除对应的图片目录
    url_hash = hashlib.md5(url.encode()).hexdigest()[:8]
    logger.info(f"查找对应的图片目录（URL hash: {url_hash}）...")
    
    if os.path.exists(IMAGE_DIR):
        found_images = False
        for img_dir in os.listdir(IMAGE_DIR):
            if img_dir.endswith(f"_{url_hash}"):
                img_path = os.path.join(IMAGE_DIR, img_dir)
                try:
                    shutil.rmtree(img_path)
                    logger.info(f"✅ 已删除图片目录: {img_path}")
                    found_images = True
                    deleted = True
                except Exception as e:
                    logger.error(f"❌ 删除图片目录失败: {img_path} | {e}")
        
        if not found_images:
            logger.info(f"⚠️  未找到对应的图片目录")
    
    # 从下载记录中移除
    del articles[url]
    try:
        with open(DOWNLOADED_FILE, "w", encoding="utf-8") as f:
            json.dump({"articles": articles}, f, indent=2, ensure_ascii=False)
        logger.info(f"✅ 已从下载记录中移除: {url}")
    except Exception as e:
        logger.error(f"❌ 更新下载记录失败: {e}")
    
    return deleted


def load_articles():
    """从配置文件加载文章URL列表（排除删除标记的）"""
    if not os.path.exists(CONFIG_FILE):
        logger.error(f"配置文件 {CONFIG_FILE} 不存在")
        return []
    
    try:
        urls = []
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                # 跳过空行、注释和删除标记
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


def process_images(html, title, url):
    logger.info("处理文章中的图片...")
    
    # 用 URL 的哈希值来区分文章，确保唯一性（即使标题重复）
    url_hash = hashlib.md5(url.encode()).hexdigest()[:8]
    safe_title = "".join(c if c.isalnum() or c in "._- " else "" for c in title).strip()
    safe_title = safe_title.replace(" ", "_") or "article"
    
    # 文件夹名格式：标题_哈希值（可读性 + 唯一性）
    article_image_dir = os.path.join(IMAGE_DIR, f"{safe_title}_{url_hash}")
    os.makedirs(article_image_dir, exist_ok=True)

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
        filepath = os.path.join(article_image_dir, filename)

        try:
            download_image(src, filepath)
            img["src"] = f"images/{safe_title}_{url_hash}/{filename}"
            logger.info(f"图片保存成功: {filename}")
        except Exception as e:
            logger.error(f"图片下载失败: {src} | {e}")

    return str(soup)


def get_next_article_number():
    """获取下一个文章编号"""
    markdown_dir = OUTPUT_DIR
    if not os.path.exists(markdown_dir):
        return 1
    
    # 查找现有的数字文件（00X.md 格式）
    files = os.listdir(markdown_dir)
    max_num = 0
    for f in files:
        if f[0].isdigit() and f.endswith('.md'):
            try:
                num = int(f.split('.')[0])
                max_num = max(max_num, num)
            except:
                pass
    
    return max_num + 1


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

    # 使用递增的数字作为文件名（更简洁的 URL）
    article_num = get_next_article_number()
    filename = f"{article_num:03d}.md"  # 001.md, 002.md 等
    filepath = os.path.join(OUTPUT_DIR, filename)

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(md_text)

    logger.info(f"Markdown 文件已生成: {filepath}")
    return filename, title  # 返回文件名和原始标题


def sync_mkdocs_nav():
    """同步 mkdocs.yml 导航：
    1. 删除 mkdocs.yml 中不存在的文件条目
    2. 添加 markdown 目录中的新文件
    3. 保证导航与实际文件完全同步
    """
    mkdocs_file = "mkdocs.yml"
    
    try:
        # 获取 markdown 目录中所有的 .md 文件
        actual_files = set()
        for file in os.listdir(OUTPUT_DIR):
            if file.endswith(".md"):
                actual_files.add(file)
        
        logger.info(f"检测到实际文件: {actual_files}")
        
        # 读取 mkdocs.yml
        with open(mkdocs_file, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
        
        if "nav" not in config:
            config["nav"] = []
        
        # 收集所有需要显示的文章（不包括 index.md）
        articles_to_show = {}  # {filename: title}
        
        # 首先遍历现有导航，收集存在的文章信息
        for item in config["nav"]:
            if isinstance(item, dict):
                for title, filename in item.items():
                    if filename != "index.md" and filename in actual_files:
                        articles_to_show[filename] = title
        
        # 然后扫描 markdown 目录，添加未在导航中的文件
        for filename in actual_files:
            if filename != "index.md" and filename not in articles_to_show:
                title = extract_title_from_file(filename)
                articles_to_show[filename] = title
                logger.info(f"✅ 发现新文章: '{title}' ({filename})")
        
        # 构建新的导航：首页 + 按文件名排序的文章
        new_nav = [{"Home": "index.md"}]
        
        for filename in sorted(articles_to_show.keys()):
            title = articles_to_show[filename]
            new_nav.append({title: filename})
        
        # 保存更新后的配置
        config["nav"] = new_nav
        with open(mkdocs_file, "w", encoding="utf-8") as f:
            yaml.dump(config, f, allow_unicode=True, default_flow_style=False)
        
        logger.info(f"✅ mkdocs.yml 已同步: 共 {len(articles_to_show)} 篇文章")
            
    except Exception as e:
        logger.error(f"❌ 同步 mkdocs.yml 失败: {e}")


def extract_title_from_file(filename):
    """从 markdown 文件中提取标题（第一行的 #）"""
    try:
        filepath = os.path.join(OUTPUT_DIR, filename)
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("#"):
                    # 移除 # 和空格，获取标题
                    title = line.lstrip("#").strip()
                    return title if title else filename.replace(".md", "")
        return filename.replace(".md", "")
    except:
        return filename.replace(".md", "")


def update_mkdocs_nav(articles_files):
    """更新 mkdocs.yml 导航，添加新文章
    articles_files: [(filename, title), ...] 元组列表
    """
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
        
        # 添加新文章到导航（使用原始标题和数字文件名）
        for filename, title in articles_files:
            if filename not in existing_files:
                new_nav.append({title: filename})
                logger.info(f"将 '{title}' 添加到导航 ({filename})")
        
        config["nav"] = new_nav
        
        # 保存更新后的配置
        with open(mkdocs_file, "w", encoding="utf-8") as f:
            yaml.dump(config, f, allow_unicode=True, default_flow_style=False)
        
        logger.info("mkdocs.yml 已更新")
    except Exception as e:
        logger.error(f"更新 mkdocs.yml 失败: {e}")


def update_index_page():
    """自动更新 index.md，生成文章列表"""
    mkdocs_file = "mkdocs.yml"
    index_file = os.path.join(OUTPUT_DIR, "index.md")
    
    try:
        # 读取 mkdocs.yml 获取所有文章
        with open(mkdocs_file, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
        
        nav = config.get("nav", [])
        
        # 生成文章列表内容
        articles_list = []
        for item in nav:
            if isinstance(item, dict):
                for title, filename in item.items():
                    if filename != "index.md":  # 跳过首页本身
                        articles_list.append((title, filename))
        
        # 构建新的 index.md 内容
        index_content = """# WeChat Articles Archive

欢迎来到微信公众号文章存档库！

这里收集了精选的微信公众号文章，转换为静态网站格式方便阅读。

## 📚 文章列表

"""
        
        if articles_list:
            for i, (title, filename) in enumerate(articles_list, 1):
                # 生成链接（去掉 .md 后缀）
                link = filename.replace(".md", "")
                index_content += f"{i}. [{title}]({link})\n"
        else:
            index_content += "暂无文章\n"
        
        # 保存更新后的 index.md
        with open(index_file, "w", encoding="utf-8") as f:
            f.write(index_content)
        
        logger.info(f"index.md 已更新，包含 {len(articles_list)} 篇文章")
    except Exception as e:
        logger.error(f"更新 index.md 失败: {e}")


def build_mkdocs():
    """运行 mkdocs build 重新构建网站"""
    try:
        logger.info("开始构建 MkDocs 网站...")
        # 在当前工作目录运行，确保找到 mkdocs.yml
        result = subprocess.run(
            ["python", "-m", "mkdocs", "build"],
            cwd=os.getcwd(),
            capture_output=True,
            text=True
        )
        
        if result.returncode == 0:
            logger.info("MkDocs 网站构建成功 ✅")
        else:
            logger.error(f"MkDocs 构建失败: {result.stderr}")
    except Exception as e:
        logger.error(f"运行 mkdocs build 出错: {e}")


def main():
    logger.info("===== 程序启动 =====")
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    has_changes = False  # 标记是否有文章改动
    article_files_to_update = []  # 新添加的文章

    # ============ 第一步：处理删除操作 ============
    logger.info("\n--- 第一步：检查是否需要删除文章 ---")
    delete_urls = load_delete_urls()
    if delete_urls:
        logger.info(f"准备删除 {len(delete_urls)} 篇文章...")
        for url in delete_urls:
            if delete_article(url):
                has_changes = True
                logger.info(f"✅ 文章已删除: {url}")
    else:
        logger.info("无需删除文章")

    # ============ 第二步：处理下载操作 ============
    logger.info("\n--- 第二步：检查是否有新文章需要下载 ---")
    urls = load_articles()
    if not urls:
        logger.warning("⚠️  没有文章 URL 需要处理")
    else:
        # 加载已下载的URL
        downloaded_urls = load_downloaded_urls()
        logger.info(f"已下载记录: {len(downloaded_urls)} 篇文章")
        
        # 过滤出未下载的URL
        urls_to_process = [url for url in urls if url not in downloaded_urls]
        
        if urls_to_process:
            logger.info(f"需要处理: {len(urls_to_process)} 篇新文章")
            
            driver = init_driver()
            
            for url in urls_to_process:
                try:
                    title, html = fetch_article_html(driver, url)
                    logger.info(f"开始处理文章: {title}")
                    html = process_images(html, title, url)
                    filename, actual_title = save_markdown(title, html)
                    article_files_to_update.append((filename, actual_title))
                    
                    # 保存已下载的URL和文件名
                    save_downloaded_url(url, filename)
                    has_changes = True
                    logger.info(f"✅ 文章完成: {title}")
                except Exception as e:
                    logger.error(f"❌ 文章处理失败: {url} | {e}")
            
            driver.quit()
            logger.info("浏览器已关闭")
        else:
            logger.info("所有文章都已下载，无新文章处理")
    # ============ 第三步：同步 mkdocs.yml ============
    logger.info("\n--- 第三步：同步 mkdocs.yml（确保导航与文件一致） ---")
    sync_mkdocs_nav()
    update_index_page()
    
    # ============ 第四步：重建网站 ============
    logger.info("\n--- 第四步：重新构建网站 ---")
    build_mkdocs()
    
    logger.info("\n===== 全部任务完成 ✅ =====")


if __name__ == "__main__":
    main()
