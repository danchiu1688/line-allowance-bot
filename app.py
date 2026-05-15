from flask import Flask, request, jsonify
import requests
import os
from datetime import datetime

app = Flask(__name__)

LINE_TOKEN = os.environ.get('LINE_TOKEN')
NOTION_TOKEN = os.environ.get('NOTION_TOKEN')
NOTION_DB_ID = os.environ.get('NOTION_DB_ID', '11863dee-85c2-485d-9ff4-9b77be2dbb33')

HELP_TEXT = (
    "📒 記帳指令：\n"
    "+金額 說明 → 記收入\n"
    "-金額 說明 → 記支出\n"
    "餘額 → 查現在有多少\n"
    "本月 → 看這個月花多少\n\n"
    "例如：+100 零用錢\n"
    "例如：-30 珍珠奶茶"
)


def line_reply(reply_token, text):
    requests.post(
        'https://api.line.me/v2/bot/message/reply',
        headers={'Authorization': f'Bearer {LINE_TOKEN}', 'Content-Type': 'application/json'},
        json={'replyToken': reply_token, 'messages': [{'type': 'text', 'text': text}]}
    )


def notion_create(name, date, record_type, amount):
    resp = requests.post(
        'https://api.notion.com/v1/pages',
        headers={
            'Authorization': f'Bearer {NOTION_TOKEN}',
            'Content-Type': 'application/json',
            'Notion-Version': '2022-06-28'
        },
        json={
            'parent': {'database_id': NOTION_DB_ID},
            'properties': {
                '名稱': {'title': [{'text': {'content': name}}]},
                '日期': {'date': {'start': date}},
                '類型': {'select': {'name': record_type}},
                '金額': {'number': amount}
            }
        }
    )
    return resp.ok


def notion_sum(record_type, start_date=None):
    headers = {
        'Authorization': f'Bearer {NOTION_TOKEN}',
        'Content-Type': 'application/json',
        'Notion-Version': '2022-06-28'
    }
    base_filter = {'property': '類型', 'select': {'equals': record_type}}
    if start_date:
        query_filter = {
            'and': [
                base_filter,
                {'property': '日期', 'date': {'on_or_after': start_date}}
            ]
        }
    else:
        query_filter = base_filter

    total = 0
    cursor = None
    while True:
        body = {'filter': query_filter, 'page_size': 100}
        if cursor:
            body['start_cursor'] = cursor
        result = requests.post(
            f'https://api.notion.com/v1/databases/{NOTION_DB_ID}/query',
            headers=headers, json=body
        ).json()
        for page in result.get('results', []):
            total += page['properties']['金額']['number'] or 0
        if not result.get('has_more'):
            break
        cursor = result.get('next_cursor')
    return total


@app.route('/webhook', methods=['POST'])
def webhook():
    body = request.get_json(silent=True) or {}
    for event in body.get('events', []):
        if event.get('type') != 'message' or event['message'].get('type') != 'text':
            continue
        text = event['message']['text'].strip()
        reply_token = event['replyToken']
        today = datetime.now().strftime('%Y-%m-%d')

        if text.startswith('+'):
            parts = text[1:].strip().split(None, 1)
            try:
                amount = int(parts[0])
                note = parts[1] if len(parts) > 1 else '收入'
                if notion_create(note, today, '收入', amount):
                    line_reply(reply_token, f'✅ 收入 +{amount} 元已記錄！')
                else:
                    line_reply(reply_token, '❌ 記錄失敗，請再試一次')
            except (ValueError, IndexError):
                line_reply(reply_token, '格式錯誤，請用：+金額 說明\n例如：+100 零用錢')

        elif text.startswith('-'):
            parts = text[1:].strip().split(None, 1)
            try:
                amount = int(parts[0])
                note = parts[1] if len(parts) > 1 else '支出'
                if notion_create(note, today, '支出', amount):
                    line_reply(reply_token, f'💸 支出 -{amount} 元已記錄！')
                else:
                    line_reply(reply_token, '❌ 記錄失敗，請再試一次')
            except (ValueError, IndexError):
                line_reply(reply_token, '格式錯誤，請用：-金額 說明\n例如：-30 珍珠奶茶')

        elif text == '餘額':
            income = notion_sum('收入')
            expense = notion_sum('支出')
            line_reply(reply_token,
                f'💰 目前餘額：{income - expense} 元\n'
                f'收入合計：{income} 元\n'
                f'支出合計：{expense} 元'
            )

        elif text == '本月':
            now = datetime.now()
            first_day = f'{now.year}-{now.month:02d}-01'
            income = notion_sum('收入', first_day)
            expense = notion_sum('支出', first_day)
            line_reply(reply_token,
                f'📊 {now.year}年{now.month}月\n'
                f'收入：{income} 元\n'
                f'支出：{expense} 元\n'
                f'結餘：{income - expense} 元'
            )

        else:
            line_reply(reply_token, HELP_TEXT)

    return jsonify({'status': 'ok'})


@app.route('/')
def index():
    return 'LINE 零用錢記帳 Bot 運行中 ✅'


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
