from flask import Flask, request, jsonify
import requests
import os
import re
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
    "⭐ 點數指令：\n"
    "+N點 說明 → 獎勵點數\n"
    "-N點 說明 → 扣除點數\n"
    "點數 → 查目前點數\n\n"
    "例如：+100 零用錢\n"
    "例如：-30 珍珠奶茶\n"
    "例如：+5點 考100分\n"
    "例如：-3點 忘記寫功課"
)

NOTION_HEADERS = {
    'Authorization': f'Bearer {NOTION_TOKEN}',
    'Content-Type': 'application/json',
    'Notion-Version': '2022-06-28'
}


def get_line_name(user_id):
    resp = requests.get(
        f'https://api.line.me/v2/bot/profile/{user_id}',
        headers={'Authorization': f'Bearer {LINE_TOKEN}'}
    )
    if resp.ok:
        return resp.json().get('displayName', '')
    return ''


def line_reply(reply_token, text):
    requests.post(
        'https://api.line.me/v2/bot/message/reply',
        headers={'Authorization': f'Bearer {LINE_TOKEN}', 'Content-Type': 'application/json'},
        json={'replyToken': reply_token, 'messages': [{'type': 'text', 'text': text}]}
    )


def notion_create(name, date, record_type, amount=None, points=None, recorder=''):
    props = {
        '名稱': {'title': [{'text': {'content': name}}]},
        '日期': {'date': {'start': date}},
        '類型': {'select': {'name': record_type}},
    }
    if amount is not None:
        props['金額'] = {'number': amount}
    if points is not None:
        props['點數'] = {'number': points}
    if recorder:
        props['記錄人'] = {'rich_text': [{'text': {'content': recorder}}]}

    resp = requests.post(
        'https://api.notion.com/v1/pages',
        headers=NOTION_HEADERS,
        json={'parent': {'database_id': NOTION_DB_ID}, 'properties': props}
    )
    if not resp.ok:
        print(f'[notion_create ERROR] {resp.status_code}: {resp.text}')
    return resp.ok


def db_sum(record_type, field, start_date=None):
    base_filter = {'property': '類型', 'select': {'equals': record_type}}
    query_filter = {
        'and': [base_filter, {'property': '日期', 'date': {'on_or_after': start_date}}]
    } if start_date else base_filter

    total = 0
    cursor = None
    while True:
        body = {'filter': query_filter, 'page_size': 100}
        if cursor:
            body['start_cursor'] = cursor
        result = requests.post(
            f'https://api.notion.com/v1/databases/{NOTION_DB_ID}/query',
            headers=NOTION_HEADERS, json=body
        ).json()
        for page in result.get('results', []):
            val = page['properties'].get(field, {}).get('number') or 0
            total += val
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
        text = text.replace('＋', '+').replace('－', '-').replace('，', ',')
        reply_token = event['replyToken']
        now = datetime.now()
        timestamp = now.strftime('%Y-%m-%dT%H:%M:%S+08:00')

        user_id = event.get('source', {}).get('userId', '')
        recorder = get_line_name(user_id) if user_id else ''

        m_reward = re.match(r'^\+(\d+)[點点]\s*(.*)$', text)
        m_deduct = re.match(r'^-(\d+)[點点]\s*(.*)$', text)

        if m_reward:
            pts = int(m_reward.group(1))
            note = m_reward.group(2).strip() or '獎勵'
            if notion_create(note, timestamp, '獎勵', points=pts, recorder=recorder):
                line_reply(reply_token, f'⭐ 獎勵 +{pts} 點已記錄！\n原因：{note}')
            else:
                line_reply(reply_token, '❌ 記錄失敗，請再試一次')

        elif m_deduct:
            pts = int(m_deduct.group(1))
            note = m_deduct.group(2).strip() or '扣點'
            if notion_create(note, timestamp, '扣點', points=pts, recorder=recorder):
                line_reply(reply_token, f'😔 扣除 -{pts} 點已記錄。\n原因：{note}')
            else:
                line_reply(reply_token, '❌ 記錄失敗，請再試一次')

        elif text == '點數':
            earned = db_sum('獎勵', '點數')
            deducted = db_sum('扣點', '點數')
            line_reply(reply_token,
                f'⭐ 目前點數：{earned - deducted} 點\n'
                f'累計獲得：{earned} 點\n'
                f'累計扣除：{deducted} 點'
            )

        elif text.startswith('+'):
            parts = text[1:].strip().split(None, 1)
            try:
                amount = int(parts[0])
                note = parts[1] if len(parts) > 1 else '收入'
                if notion_create(note, timestamp, '收入', amount=amount, recorder=recorder):
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
                if notion_create(note, timestamp, '支出', amount=amount, recorder=recorder):
                    line_reply(reply_token, f'💸 支出 -{amount} 元已記錄！')
                else:
                    line_reply(reply_token, '❌ 記錄失敗，請再試一次')
            except (ValueError, IndexError):
                line_reply(reply_token, '格式錯誤，請用：-金額 說明\n例如：-30 珍珠奶茶')

        elif text == '餘額':
            income = db_sum('收入', '金額')
            expense = db_sum('支出', '金額')
            line_reply(reply_token,
                f'💰 目前餘額：{income - expense} 元\n'
                f'收入合計：{income} 元\n'
                f'支出合計：{expense} 元'
            )

        elif text == '本月':
            first_day = f'{now.year}-{now.month:02d}-01'
            income = db_sum('收入', '金額', first_day)
            expense = db_sum('支出', '金額', first_day)
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
