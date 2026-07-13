# 🏊 UMD 开放时间 · UMD Facility Hours

自动抓取 UMD 游泳馆(Natatorium)、食堂(Dining Halls)、咖啡厅(IDEA Factory)的每周开放时间。

Auto-scraped weekly hours for UMD Natatorium, Dining Halls, and IDEA Factory.

## 📡 数据来源 Sources

| 数据 | 来源 |
|------|------|
| 🏊 Natatorium | [RecWell Facility Alerts](https://recwell.umd.edu/facility-alerts) |
| 🍽️ Dining Halls | [UMD Dining](https://dining.umd.edu/hours-locations/dining-halls) |
| ☕ IDEA Factory | [UMD Cafes](https://dining.umd.edu/hours-locations/cafes) |

## 🔄 自动更新 Auto-update

通过 GitHub Actions 每 **12 小时**自动抓取一次数据。
The scraper runs every **12 hours** via GitHub Actions.

## 🛠 技术栈 Tech Stack

- Python (`requests` + `BeautifulSoup`) — 网页爬虫
- GitHub Actions — 定时任务
- GitHub Pages — 静态托管
- Vanilla HTML/CSS/JS — 前端展示

## 📁 文件结构

```
├── .github/workflows/scrape.yml   ← GitHub Actions 定时任务
├── scrape.py                       ← Python 爬虫
├── data.json                       ← 爬取结果（自动生成）
├── index.html                      ← 前端展示页
└── style.css                       ← 样式
```

## 🚀 本地运行

```bash
pip install requests beautifulsoup4
python scrape.py
# 然后用浏览器打开 index.html
```
