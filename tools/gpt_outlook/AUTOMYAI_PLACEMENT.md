# AutoMyAI placement

This is the GPT (ChatGPT/Outlook) registration tool type, parallel to Grok TTK:

- Grok: `tools/grok_ttk`
- GPT:  `tools/gpt_outlook`

Start WebUI (example):
```bash
cd /opt/automyai/tools/gpt_outlook
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python start_webui.py --host 127.0.0.1 --port 8765
```

Runtime data suggested under:
`/opt/automyai/data/gpt_outlook`
