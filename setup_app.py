"""Local setup UI. Secrets stay in memory, then go directly to service APIs."""
from __future__ import annotations
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import threading
import time
import uuid
from urllib import request, error, parse
import webbrowser
from network import https_handler, connection_message

ROOT = Path(__file__).resolve().parent
SESSION = secrets.token_urlsafe(32)
STATE = {'running': False, 'steps': [], 'url': '', 'done': False}
LOCK = threading.Lock()

class SetupError(Exception): pass
class APIError(SetupError):
    def __init__(self, service, code):
        self.code = code
        super().__init__(f'{service}: HTTP {code}. Проверьте доступ и разрешения токена.')

class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, *args): return None

def api(url, method='GET', data=None, token=None):
    headers = {'User-Agent': 'ChinaNoveltiesSetup/2', 'Accept': 'application/json'}
    if token: headers['Authorization'] = 'Bearer ' + token
    if data is not None: headers['Content-Type'] = 'application/json'
    req = request.Request(url, data=None if data is None else json.dumps(data).encode(), headers=headers, method=method)
    try:
        with request.build_opener(NoRedirect(), https_handler()).open(req, timeout=30) as r:
            raw = r.read(12_000_000)
            return json.loads(raw) if raw else {}
    except error.HTTPError as exc:
        raise APIError('GitHub' if 'api.github.com' in url else 'Telegram', exc.code) from None
    except (error.URLError, OSError) as exc:
        raise SetupError(connection_message(url, exc)) from None

class GitHub:
    def __init__(self, token): self.token = token
    def call(self, path, method='GET', data=None):
        return api('https://api.github.com' + path, method, data, self.token)
    def variable(self, repo, name, value):
        try: self.call(f'/repos/{repo}/actions/variables/{name}', 'PATCH', {'name':name,'value':value})
        except APIError as exc:
            if exc.code != 404: raise
            self.call(f'/repos/{repo}/actions/variables', 'POST', {'name':name,'value':value})
    def secret(self, repo, name, value):
        from nacl.public import PublicKey, SealedBox
        key = self.call(f'/repos/{repo}/actions/secrets/public-key')
        encrypted = SealedBox(PublicKey(base64.b64decode(key['key']))).encrypt(value.encode())
        self.call(f'/repos/{repo}/actions/secrets/{name}', 'PUT', {'key_id':key['key_id'], 'encrypted_value':base64.b64encode(encrypted).decode()})


def step(message):
    with LOCK: STATE['steps'].append(message)


def validate(values):
    for key in (('github_token','repo') if values.get('update_only') else ('github_token','bot_token','channel','repo')):
        if not isinstance(values.get(key),str) or not values[key].strip():
            raise SetupError('Заполните GitHub-токен, токен бота, канал и имя репозитория.')
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}',values['repo']):
        raise SetupError('Имя репозитория: латинские буквы, цифры, точка, дефис.')
    if values['repo'] in ('.','..'): raise SetupError('Некорректное имя репозитория.')
    if not values.get('update_only') and not re.fullmatch(r'(?:@[A-Za-z0-9_]{5,}|-100[0-9]+)',values['channel']):
        raise SetupError('Введите @имя канала или числовой ID, начинающийся с -100.')
    if not values.get('update_only') and not values.get('terms_ok'):
        raise SetupError('Нужно проверить условия Modian и подтвердить разрешение на сбор.')
    if not values.get('update_only') and not values.get('public_ok'): raise SetupError('Подтвердите создание публичного репозитория.')


def telegram_check(token, channel):
    def tg(method, data=None):
        result = api(f'https://api.telegram.org/bot{token}/{method}', 'POST', data or {})
        if not result.get('ok'): raise SetupError('Telegram отклонил запрос. Проверьте бота и канал.')
        return result['result']
    bot = tg('getMe')
    chat = tg('getChat', {'chat_id':channel})
    if chat['type'] != 'channel': raise SetupError('Укажите Telegram-канал, а не группу или личный чат.')
    member = tg('getChatMember', {'chat_id':chat['id'],'user_id':bot['id']})
    if member.get('status') != 'creator' and not (member.get('status') == 'administrator' and member.get('can_post_messages')):
        raise SetupError('Добавьте этого бота администратором канала с правом публикации.')
    return str(chat['id'])


def project_files(values=None):
    allowed = ['parser.py','setup_app.py','network.py','config.json','rights.json','requirements.txt','.gitignore','.china-parser','README.md']
    paths = [ROOT/p for p in allowed]
    paths += list((ROOT/'scripts').glob('*.py')) + list((ROOT/'scripts').glob('*.sh'))
    paths += list((ROOT/'tests').glob('*.py')) + list((ROOT/'.github/workflows').glob('*.yml'))
    files = {str(p.relative_to(ROOT)):p.read_text() for p in paths}
    if values:
        cfg = json.loads(files['config.json'])
        for source in cfg['sources']:
            if source['name'] == 'modian': source['terms_reviewed'] = bool(values.get('terms_ok'))
        files['config.json'] = json.dumps(cfg,ensure_ascii=False,indent=2)
    return files


def upload(gh, repo, branch, values=None):
    ref = gh.call(f'/repos/{repo}/git/ref/heads/{parse.quote(branch,safe="")}')
    parent = ref['object']['sha']
    commit = gh.call(f'/repos/{repo}/git/commits/{parent}')
    files = project_files(values)
    if values and values.get('update_only'):
        # Existing source choices and editorial overrides belong to the deployed project.
        files.pop('config.json', None)
        files.pop('rights.json', None)
    entries = [{'path':name,'mode':'100644','type':'blob','content':body} for name,body in files.items()]
    tree = gh.call(f'/repos/{repo}/git/trees','POST',{'base_tree':commit['tree']['sha'],'tree':entries})
    new = gh.call(f'/repos/{repo}/git/commits','POST',{'message':'Install China Novelties collector','tree':tree['sha'],'parents':[parent]})
    gh.call(f'/repos/{repo}/git/refs/heads/{parse.quote(branch,safe="")}', 'PATCH', {'sha':new['sha'],'force':False})


def deploy(values):
    try:
        validate(values)
        if not values.get('update_only'):
            step('Проверяю бота и право публикации в существующий канал…')
            channel = telegram_check(values['bot_token'], values['channel'])
        gh = GitHub(values['github_token'])
        login = gh.call('/user')['login']
        repo = login + '/' + values['repo']
        step('Проверяю GitHub и подготавливаю репозиторий…')
        try:
            metadata = gh.call('/repos/' + repo)
            try: gh.call('/repos/' + repo + '/contents/.china-parser')
            except APIError:
                raise SetupError('Это существующий чужой проект. Выберите новое имя репозитория, чтобы не менять его файлы.') from None
            if metadata['private']: raise SetupError('Этот репозиторий приватный. Выберите новый публичный для бесплатных стандартных runners.')
        except APIError as exc:
            if exc.code != 404: raise
            if values.get('update_only'): raise SetupError('Репозиторий для обновления не найден. Проверьте имя и GitHub-токен.') from None
            metadata = gh.call('/user/repos','POST',{'name':values['repo'],'private':False,'auto_init':True,'description':'Autonomous Chinese product news for Telegram'})
        branch = metadata['default_branch']
        with LOCK: STATE['url'] = 'https://github.com/' + repo + '/actions'
        if values.get('update_only'):
            names = {item['name'] for item in gh.call(f'/repos/{repo}/actions/secrets?per_page=100').get('secrets', [])}
            if not {'TELEGRAM_BOT_TOKEN','TELEGRAM_CHAT_ID'}.issubset(names):
                raise SetupError('В репозитории нет сохранённых реквизитов Telegram. Используйте обычную настройку.')
        gh.variable(repo, 'PUBLISH_ENABLED', 'false')
        upload(gh, repo, branch, values)
        if values.get('update_only'):
            step('Использую ранее сохранённые реквизиты Telegram. Канал и история сохранены.')
        else:
            step('Сохраняю реквизиты Telegram в зашифрованных GitHub Secrets…')
            gh.secret(repo,'TELEGRAM_BOT_TOKEN',values['bot_token'])
            gh.secret(repo,'TELEGRAM_CHAT_ID',channel)
            values['bot_token'] = ''
        step('Запускаю сбор, перевод и первый реальный пост на GitHub…')
        setup_id = uuid.uuid4().hex
        # Workflow registration can lag behind the first commit.
        for attempt in range(8):
            try:
                gh.call(f'/repos/{repo}/actions/workflows/parser.yml/dispatches','POST',{'ref':branch,'inputs':{'mode':'activate','setup_id':setup_id}})
                break
            except APIError as exc:
                if exc.code != 404 or attempt == 7: raise
                time.sleep(5)
        deadline = time.monotonic() + 900
        while time.monotonic() < deadline:
            runs = gh.call(f'/repos/{repo}/actions/workflows/parser.yml/runs?event=workflow_dispatch&per_page=30')['workflow_runs']
            matched = next((r for r in runs if setup_id in r.get('display_title','')), None)
            if matched and matched['status'] == 'completed':
                if matched.get('conclusion') != 'success':
                    jobs = gh.call(f'/repos/{repo}/actions/runs/{matched["id"]}/jobs').get('jobs', [])
                    failed_steps = [s['name'] for j in jobs for s in j.get('steps', []) if s.get('conclusion') in ('failure','timed_out')]
                    step('Ошибка на шаге: ' + ', '.join(failed_steps or [matched.get('conclusion','unknown')]))
                    with LOCK: STATE['url'] = matched['html_url']
                    raise SetupError('Проверка не пройдена; расписание выключено. Откройте указанный шаг в GitHub Actions — причины пропуска товаров теперь выведены в журнал.')
                gh.variable(repo,'PUBLISH_ENABLED','true')
                step('Первый пост отправлен. Расписание каждые 4 часа включено; Mac можно выключить.')
                with LOCK: STATE['done'] = True
                return
            time.sleep(10)
        raise SetupError('Ожидание проверки истекло. Расписание не включено. Посмотрите результат в GitHub Actions.')
    except SetupError as exc:
        step(str(exc))
    except Exception as exc:
        step('Установка остановлена: ' + type(exc).__name__ + '. Токены не записаны в журнал.')
    finally:
        values.clear()
        with LOCK: STATE['running'] = False


PAGE = '''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Китайские новинки · Запуск</title>
<style>body{font:16px/1.5 system-ui;margin:0;background:#f3f5f6;color:#17272b}main{max-width:720px;margin:48px auto;padding:32px;background:white;border-radius:24px}small,.muted{color:#617276}h1{font-size:32px;line-height:1.15}label{display:block;margin:18px 0 5px}input:not([type=checkbox]){box-sizing:border-box;width:100%;padding:13px;border:1px solid #c8d3d5;border-radius:9px;font:inherit}button{background:#0b6854;color:white;border:0;border-radius:12px;padding:15px 24px;font:600 17px system-ui;margin-top:24px;cursor:pointer}button:disabled{opacity:.5}.notice{padding:15px;background:#fff5d9;border-radius:12px}a{color:#087660}li{margin-bottom:9px}#result{white-space:pre-wrap}#link{display:block;margin-top:15px}</style>
<main><small>CHINA NOVELTIES / НАСТРОЙКА</small><h1>Подключить канал.<br>Запустить одной кнопкой.</h1><p class="muted">Один раз введите доступы. Установщик создаст проект, настроит секреты, запустит проверку и включит расписание только после первого успешного поста.</p>
<div class="notice"><b>Текущая сборка ещё требует проверки источников.</b> Xiaomi отключён. Если нет доступных фото или каталога, установка покажет причину и не объявит парсер работающим.</div>
<form id="form"><label><input type="checkbox" name="update_only" id="update_only"> Обновить установленный проект — использовать сохранённые Telegram Secrets</label><label>Токен GitHub</label><input name="github_token" type="password" autocomplete="off" required><small><a href="https://github.com/settings/tokens/new?scopes=repo,workflow&description=China%20Novelties%20Setup" target="_blank" rel="noreferrer">Создать токен для установки</a> · права repo и workflow. Можно отозвать после успешной настройки.</small>
<label>Токен существующего Telegram-бота</label><input name="bot_token" type="password" autocomplete="off" required><small>Из @BotFather. Бот должен быть администратором канала.</small>
<label>Telegram-канал</label><input name="channel" placeholder="@your_channel или -100…" required>
<label>Имя GitHub-проекта</label><input name="repo" value="china-novelties-telegram" required>
<label><input type="checkbox" name="public_ok" required> Создать публичный проект для бесплатных запусков. Код и история публикаций будут открыты, токен бота хранится отдельно в Secrets.</label>
<details><summary>Настройки источника</summary><label><input type="checkbox" name="terms_ok" required> Условия Modian проверены; автоматический сбор допустим.</label><small>Фотографии автоматически берутся из карточек товаров. Доступность фото не означает наличие лицензии на их использование.</small></details><button id="submit">Настроить и проверить запуск</button></form><h3>Ход настройки</h3><div id="result">Ожидаю реквизиты. Ничего ещё не опубликовано.</div><a id="link" hidden target="_blank" rel="noreferrer">Открыть GitHub Actions</a><p class="muted">Токены передаются только в GitHub и Telegram. Этот экран работает локально на вашем Mac.</p></main>
<script>const session=location.hash.slice(1);history.replaceState(null,'',location.pathname);const headers={'X-Setup-Session':session,'Content-Type':'application/json'};let busy=false;document.querySelector('#update_only').onchange=e=>{const f=document.querySelector('#form');for(const name of ['bot_token','channel','terms_ok','public_ok']){f[name].required=!e.target.checked;}f.bot_token.disabled=e.target.checked;f.channel.disabled=e.target.checked;};
async function poll(){try{const r=await fetch('/status',{headers});const s=await r.json();document.querySelector('#result').textContent=s.steps.join('\\n')||'Ожидаю реквизиты.';document.querySelector('#submit').disabled=s.running;if(s.url){const a=document.querySelector('#link');a.href=s.url;a.hidden=false;}if(s.done){document.querySelector('#form').hidden=true;}}catch(e){document.querySelector('#result').textContent='Связь с установщиком потеряна. Откройте START.command повторно.'}}
document.querySelector('#form').onsubmit=async e=>{e.preventDefault();if(busy)return;busy=true;document.querySelector('#submit').disabled=true;const form=e.target;const values=Object.fromEntries(new FormData(form));values.update_only=form.update_only.checked;values.public_ok=form.public_ok.checked;values.terms_ok=form.terms_ok.checked;try{const r=await fetch('/start',{method:'POST',headers,body:JSON.stringify(values)});const data=await r.json();if(!r.ok)document.querySelector('#result').textContent=data.error;else{form.github_token.value='';form.bot_token.value='';await poll();}}catch(e){document.querySelector('#result').textContent='Не удалось связаться с установщиком.'}finally{busy=false;}};setInterval(poll,2000);poll();</script></html>'''

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def respond(self,status,body,ctype='application/json'):
        raw = body.encode() if isinstance(body,str) else json.dumps(body,ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type',ctype+'; charset=utf-8')
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('X-Frame-Options','DENY')
        self.send_header('Content-Length',str(len(raw)))
        self.end_headers(); self.wfile.write(raw)
    def authorized(self):
        host = f'127.0.0.1:{self.server.server_port}'
        return (self.headers.get('Host') == host and self.headers.get('Origin') in (None,'http://'+host)
                and secrets.compare_digest(self.headers.get('X-Setup-Session',''),SESSION))
    def do_GET(self):
        if self.path == '/' and self.headers.get('Host') == f'127.0.0.1:{self.server.server_port}':
            return self.respond(200,PAGE,'text/html')
        if not self.authorized(): return self.respond(403,{'error':'Недействительная сессия.'})
        if self.path == '/status':
            with LOCK: return self.respond(200,STATE)
        self.respond(404,{})
    def do_POST(self):
        if not self.authorized(): return self.respond(403,{'error':'Недействительная сессия.'})
        if self.path != '/start': return self.respond(404,{})
        try:
            size = int(self.headers.get('Content-Length','0'))
            if not 0 < size < 20000: raise ValueError()
            values = json.loads(self.rfile.read(size))
            validate(values)
        except (ValueError,TypeError,SetupError) as exc:
            return self.respond(400,{'error':str(exc) if isinstance(exc,SetupError) else 'Некорректные данные.'})
        with LOCK:
            if STATE['running']: return self.respond(409,{'error':'Установка уже идёт.'})
            STATE.update(running=True,steps=[],url='',done=False)
        threading.Thread(target=deploy,args=(values,),daemon=True).start()
        self.respond(202,{'started':True})

if __name__ == '__main__':
    server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
    url = f'http://127.0.0.1:{server.server_port}/#{SESSION}'
    print('Установщик открыт в браузере. Оставьте это окно открытым до завершения.')
    webbrowser.open(url)
    try: server.serve_forever()
    except KeyboardInterrupt: server.server_close()
