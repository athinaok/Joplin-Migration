#!/usr/bin/env python3
# syntax python keep-to-enex.py Keep\*.html -o keep.enex

import argparse
import sys
import re
import parsedatetime as pdt
import time
import glob
import hashlib
import base64
import os

cal = pdt.Calendar()

# --- REGEXES ---
r1 = re.compile(r'<li class="listitem checked"><span class="bullet">&#9745;</span>.*?<span class="text">(.*?)</span>.*?</li>')
r2 = re.compile(r'<li class="listitem"><span class="bullet">&#9744;</span>.*?<span class="text">(.*?)</span>.*?</li>')
r3 = re.compile(r'<span class="chip label"><span class="label-name">([^<]*)</span>[^<]*</span>')
r4_base64 = re.compile(r'<img[^>]+src="data:(.*?);(.*?),(.*?)"')
r4_file = re.compile(r'<img[^>]+src="([^"]+)"')
r5 = re.compile(r'<div class="content">(.*)</div>')


def readlineUntil(file, text):
    line = ""
    while text not in line:
        line = file.readline()
    return line


def readImagesFromAttachment(line, base_path):
    result = ()

    # --- BASE64 IMAGES ---
    for m in r4_base64.finditer(line):
        mime = m.group(1)
        encoding = m.group(2)
        data = m.group(3)

        h = hashlib.md5(base64.b64decode(data.encode("utf-8")))

        content = f'<div><en-media type="{mime}" hash="{h.hexdigest()}" /></div>'
        resource = f'''
<resource>
<data encoding="{encoding}">{data}</data>
<mime>{mime}</mime>
</resource>
'''
        result += (content, resource)

    # --- FILE-BASED IMAGES ---
    for img_path in r4_file.findall(line):
        if img_path.startswith("data:"):
            continue

        full_path = os.path.join(base_path, img_path)
        if not os.path.exists(full_path):
            continue

        with open(full_path, "rb") as f:
            data = base64.b64encode(f.read()).decode("utf-8")

        ext = os.path.splitext(img_path)[1].lower().replace('.', '')
        mime = f"image/{ext}"
        h = hashlib.md5(base64.b64decode(data))

        content = f'<div><en-media type="{mime}" hash="{h.hexdigest()}" /></div>'
        resource = f'''
<resource>
<data encoding="base64">{data}</data>
<mime>{mime}</mime>
<resource-attributes>
<file-name>{os.path.basename(img_path)}</file-name>
</resource-attributes>
</resource>
'''
        result += (content, resource)

    return result


def mungefile(fn):
    base_path = os.path.dirname(fn)
    fp = open(fn, 'r', encoding="utf8")

    title = readlineUntil(fp, "<title>").strip()
    title = title.replace('<title>', '').replace('</title>', '')

    readlineUntil(fp, "<body>")
    t = fp.readline()
    tags = ''
    resources = ''
    if '"archived"' in t:
        tags = '<tag>archived</tag>'
    fp.readline()

    date = fp.readline().strip().replace('</div>', '')
    dt, _ = cal.parse(date)
    iso = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime(time.mktime(dt)))

    fp.readline()

    content = fp.readline()
    m = r5.search(content)
    if m:
        content = m.group(1)

    for line in fp:
        line = line.strip()
        if line == '</div></body></html>':
            break
        elif line.startswith('<div class="chips">'):
            continue
        elif '<img' in line:
            result = readImagesFromAttachment(line, base_path)
            i = 0
            while i < len(result):
                content += result[i]
                resources += result[i + 1]
                i += 2
        else:
            content += line + '\n'

    content = content.replace('<br>', '<br/>')

    while True:
        m = r1.search(content)
        if not m:
            break
        content = content[:m.start()] + '<en-todo checked="true"/>' + m.group(1) + '<br/>' + content[m.end():]

    while True:
        m = r2.search(content)
        if not m:
            break
        content = content[:m.start()] + '<en-todo checked="false"/>' + m.group(1) + '<br/>' + content[m.end():]

    m = r3.search(content)
    if m:
        content = content[:m.start()] + content[m.end():]
        tags = '<tag>' + m.group(1) + '</tag>'

    fp.close()

    print(f'''
  <note>
    <title>{title}</title>
    <content><![CDATA[
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE en-note SYSTEM "http://xml.evernote.com/pub/enml2.dtd">
<en-note>{content}</en-note>
]]></content>
    <created>{iso}</created>
    <updated>{iso}</updated>
    {tags}
    {resources}
  </note>
''', file=fxt)


# --- ARGUMENTS ---
parser = argparse.ArgumentParser()
parser.add_argument("-o", "--output", default="keep.enex")
parser.add_argument("htmlSource", nargs="*", default=["*.html"])
args = parser.parse_args()

files = []
for pattern in args.htmlSource:
    files.extend(glob.glob(pattern))

if not files:
    print("No HTML files found.")
    sys.exit(1)

fxt = open(args.output, "w", encoding="utf8")

print('''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE en-export SYSTEM "http://xml.evernote.com/pub/evernote-export3.dtd">
<en-export>''', file=fxt)

for f in files:
    mungefile(f)

print('</en-export>', file=fxt)
fxt.close()
