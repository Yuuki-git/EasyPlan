import codecs
import re

file_path = 'frontend/src/components/TaskBoard.tsx'

with codecs.open(file_path, 'r', 'utf-8', errors='ignore') as f:
    content = f.read()

# Fix header title
content = re.sub(
    r'\{currentViewBucket === \'my_day\' \? \'.*?\' : \'.*?\'\}',
    r"{currentViewBucket === 'my_day' ? '☀️ 我的一天' : '📅 计划中'}",
    content
)

# Fix header button
content = re.sub(
    r'\{isGenerating \? \'.*?\' : \'.*?\'\}',
    r"{isGenerating ? '返回当前意图' : '新计划'}",
    content
)

# Fix empty state p tag
content = re.sub(
    r'\{currentViewBucket === \'planned\'\s*\?\s*".*?"\s*:\s*".*?"\}',
    r"{currentViewBucket === 'planned' ? '您的专属空间空空如也。点击右上角，让 AI 为您分忧。' : '今天的事情都搞定啦！去喝杯茶，享受生活吧 ☕️'}",
    content
)

# Fix Fog of War button
content = re.sub(
    r'<Sparkles size=\{18\} /> \{isGenerating \? \'.*?\' : \'.*?\'\}',
    r"<Sparkles size={18} /> {isGenerating ? '正在生成下一阶段计划...' : '当前阶段已完成，让 AI 生成下一阶段计划'}",
    content
)

with codecs.open(file_path, 'w', 'utf-8') as f:
    f.write(content)
print('Fixed corrupted strings via regex')
