from flask import Flask, render_template, request, redirect, url_for, session, jsonify
import json
import os
import subprocess
import random
import string
import uuid
from datetime import datetime, timedelta
import sys
import shutil
import threading
import time
import zipfile
import psutil

app = Flask(__name__)
app.secret_key = 'sadnan-super-secret-key-2026'
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=365)  # 1 bochor login thakbe
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_HTTPONLY'] = True

USERS_FILE = 'users.json'
BOTS_DIR = 'bots'
CPU_HISTORY = {}
CRASH_COUNT = {}
NET_STATS = {}

os.makedirs(BOTS_DIR, exist_ok=True)

# ============================================
# রেট লিমিট
# ============================================

class RateLimiter:
    def check_rate(self, server_id, limit_percent):
        if server_id not in CPU_HISTORY:
            CPU_HISTORY[server_id] = []
        users = load_users()
        server = None
        for uname, data in users.items():
            if uname == 'admin': continue
            servers = data.get('servers', [])
            if not isinstance(servers, list): continue
            for s in servers:
                if isinstance(s, dict) and s.get('server_id') == server_id:
                    server = s
                    break
        if not server or server.get('status') != 'running':
            return False, 0
        pid = server.get('pid')
        if not pid: return False, 0
        try:
            proc = psutil.Process(pid)
            cpu = proc.cpu_percent(interval=1)
            now = time.time()
            CPU_HISTORY[server_id].append({'time': now, 'cpu': cpu})
            CPU_HISTORY[server_id] = [h for h in CPU_HISTORY[server_id] if now - h['time'] < 30]
            recent = [h['cpu'] for h in CPU_HISTORY[server_id] if now - h['time'] < 10]
            if recent:
                avg_cpu = sum(recent) / len(recent)
                if avg_cpu > limit_percent:
                    return True, avg_cpu
        except: pass
        return False, 0

rate_limiter = RateLimiter()

# ============================================
# অটো-রিস্টার্ট
# ============================================

def should_auto_restart(server_id):
    if server_id not in CRASH_COUNT:
        CRASH_COUNT[server_id] = {'count': 0, 'last_crash': time.time()}
    crash_info = CRASH_COUNT[server_id]
    if time.time() - crash_info['last_crash'] < 60:
        if crash_info['count'] >= 3:
            return False
    else:
        crash_info['count'] = 0
    crash_info['count'] += 1
    crash_info['last_crash'] = time.time()
    return True

# ============================================
# হেল্পার
# ============================================

def generate_random_password(length=10):
    chars = string.ascii_letters + string.digits
    return ''.join(random.choices(chars, k=length))

def load_users():
    if not os.path.exists(USERS_FILE):
        default = {"admin": {"password": "admin123", "role": "admin"}}
        save_users(default)
        return default
    with open(USERS_FILE, 'r', encoding='utf-8') as f:
        data = json.load(f)
    if 'admin' not in data:
        data['admin'] = {"password": "admin123", "role": "admin"}
        save_users(data)
    return data

def save_users(data):
    with open(USERS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

def get_server_dir(server_id):
    server_dir = os.path.join(BOTS_DIR, server_id)
    os.makedirs(server_dir, exist_ok=True)
    return server_dir

def check_server_valid(server_id):
    users = load_users()
    for uname, data in users.items():
        if uname == 'admin': continue
        servers = data.get('servers', [])
        if not isinstance(servers, list): continue
        for s in servers:
            if isinstance(s, dict) and s.get('server_id') == server_id:
                expiry = s.get('expiry', '')
                if expiry:
                    try:
                        exp_date = datetime.strptime(expiry, '%Y-%m-%d %H:%M:%S.%f')
                        if datetime.now() > exp_date:
                            return False, "expired"
                    except: pass
                return True, s
    return False, "deleted"

def get_server_by_id(server_id):
    users = load_users()
    for uname, data in users.items():
        if uname == 'admin': continue
        servers = data.get('servers', [])
        if not isinstance(servers, list): continue
        for s in servers:
            if isinstance(s, dict) and s.get('server_id') == server_id:
                return s, uname
    return None, None

def create_default_files(server_dir):
    main_py = os.path.join(server_dir, 'main.py')
    if not os.path.exists(main_py):
        with open(main_py, 'w', encoding='utf-8') as f:
            f.write('''# SADNAN HOSTING - Default Bot
import time

print("=" * 40)
print("Bot is running on SADNAN HOSTING")
print("Server is ready!")
print("=" * 40)

counter = 0
while True:
    counter += 1
    print(f"[{time.strftime('%H:%M:%S')}] Heartbeat #{counter} | Server active")
    time.sleep(10)
''')
    
    req_file = os.path.join(server_dir, 'requirements.txt')
    if not os.path.exists(req_file):
        with open(req_file, 'w', encoding='utf-8') as f:
            f.write('# Add your pip packages here\n')

# ============================================
# বট রান
# ============================================

def run_bot(server_id, main_file='main.py', requirements_file='requirements.txt'):
    server_dir = get_server_dir(server_id)
    main_path = os.path.join(server_dir, main_file)
    log_file = os.path.join(server_dir, 'output.log')
    python_exe = sys.executable
    
    def log(msg):
        try:
            with open(log_file, 'a', encoding='utf-8') as f:
                f.write(f"{msg}\n")
                f.flush()
        except: pass
    
    if not os.path.exists(main_path):
        return None, f"ERROR: {main_file} not found!"
    
    if os.path.exists(log_file):
        try: os.remove(log_file)
        except: open(log_file, 'w').close()
    
    ts = lambda: datetime.now().strftime('%I:%M:%S %p')
    
    server, _ = get_server_by_id(server_id)
    cpu_limit = server.get('cpu_limit', 80) if server else 80
    log(f"[{ts()}] Checking rate limit...")
    log(f"[{ts()}] Rate limit: {cpu_limit}%")
    log("")
    
    if requirements_file and requirements_file.strip():
        req_path = os.path.join(server_dir, requirements_file.strip())
        log(f"[{ts()}] Run: pip install -r {requirements_file}")
        log("")
        
        if os.path.exists(req_path):
            with open(req_path, 'r', encoding='utf-8') as f:
                content = f.read().strip()
            
            lines = [l.strip() for l in content.split('\n') if l.strip() and not l.strip().startswith('#')]
            
            if lines:
                try:
                    proc = subprocess.Popen(
                        [python_exe, '-m', 'pip', 'install', '-r', os.path.abspath(req_path), '--disable-pip-version-check'],
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, bufsize=1, universal_newlines=True,
                        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
                    )
                    
                    for line in iter(proc.stdout.readline, ''):
                        if line.strip():
                            log(f"[{ts()}] {line.rstrip()}")
                    
                    proc.wait()
                    log("")
                    
                    if proc.returncode != 0:
                        log(f"[{ts()}] Some packages failed to install")
                    else:
                        log(f"[{ts()}] Requirements installation complete!")
                except Exception as e:
                    log(f"[{ts()}] pip error: {str(e)}")
            else:
                log(f"[{ts()}] {requirements_file} is empty, skipping...")
        else:
            log(f"[{ts()}] {requirements_file} not found, skipping...")
    else:
        log(f"[{ts()}] No requirements file set, skipping...")
    
    log("")
    log(f"[{ts()}] Run: python {main_file}")
    log(f"[{ts()}] Python {sys.version.split()[0]}")
    log("")
    
    try:
        main_path_abs = os.path.abspath(main_path)
        env = os.environ.copy()
        env['PYTHONIOENCODING'] = 'utf-8'
        env['PYTHONUNBUFFERED'] = '1'
        
        proc = subprocess.Popen(
            [python_exe, main_path_abs],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            cwd=server_dir,
            text=True, encoding='utf-8', errors='replace',
            bufsize=1, env=env, universal_newlines=True,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
        )
        
        log(f"[{ts()}] Server marked as running")
        log(f"[{ts()}] PID: {proc.pid}")
        log("")
        
        def rate_monitor():
            while proc.poll() is None:
                time.sleep(5)
                exceeded, avg_cpu = rate_limiter.check_rate(server_id, cpu_limit)
                if exceeded:
                    log(f"[{datetime.now().strftime('%I:%M:%S %p')}] CPU Limit! {avg_cpu:.1f}% > {cpu_limit}%")
                    proc.terminate()
                    time.sleep(2)
                    if proc.poll() is None: proc.kill()
                    
                    users = load_users()
                    for uname, data in users.items():
                        if uname == 'admin': continue
                        servers = data.get('servers', [])
                        if not isinstance(servers, list): continue
                        for s in servers:
                            if isinstance(s, dict) and s.get('server_id') == server_id:
                                s['status'] = 'stopped'
                                s['pid'] = None
                                s['rate_limit_exceeded'] = True
                                s['stopped_by_user'] = False
                                save_users(users)
                                break
                    break
        
        threading.Thread(target=rate_monitor, daemon=True).start()
        
        def stream_output():
            try:
                with open(log_file, 'a', encoding='utf-8') as f:
                    for line in iter(proc.stdout.readline, ''):
                        if line:
                            line = line.rstrip('\n\r')
                            if line:
                                f.write(f"[{datetime.now().strftime('%I:%M:%S %p')}] {line}\n")
                                f.flush()
            except: pass
        
        threading.Thread(target=stream_output, daemon=True).start()
        
        return proc.pid, None
        
    except Exception as e:
        log(f"[{ts()}] Error: {str(e)}")
        return None, str(e)

def stop_bot_process(pid):
    try:
        if sys.platform == 'win32':
            subprocess.run(['taskkill', '/F', '/PID', str(pid)], capture_output=True)
        else:
            os.kill(pid, 15)
        return True
    except: return False

def monitor_bot(server_id, pid):
    while True:
        try:
            if sys.platform == 'win32':
                result = subprocess.run(['tasklist', '/FI', f'PID eq {pid}'], capture_output=True, text=True)
                if str(pid) not in result.stdout:
                    break
            else:
                try: os.kill(pid, 0)
                except: break
        except: break
        time.sleep(5)
    
    server, _ = get_server_by_id(server_id)
    if not server: return
    if server.get('stopped_by_user'): return
    if server.get('rate_limit_exceeded'): return
    
    if should_auto_restart(server_id):
        time.sleep(3)
        new_pid, error = run_bot(server_id, server.get('main_file', 'main.py'), 
                                 server.get('requirements_file', 'requirements.txt'))
        if new_pid:
            users = load_users()
            for uname, data in users.items():
                if uname == 'admin': continue
                servers = data.get('servers', [])
                if not isinstance(servers, list): continue
                for s in servers:
                    if isinstance(s, dict) and s.get('server_id') == server_id:
                        s['status'] = 'running'
                        s['pid'] = new_pid
                        s['started_at'] = str(datetime.now())
                        s['rate_limit_exceeded'] = False
                        s['stopped_by_user'] = False
                        save_users(users)
                        break
            threading.Thread(target=monitor_bot, args=(server_id, new_pid), daemon=True).start()
    else:
        users = load_users()
        for uname, data in users.items():
            if uname == 'admin': continue
            servers = data.get('servers', [])
            if not isinstance(servers, list): continue
            for s in servers:
                if isinstance(s, dict) and s.get('server_id') == server_id:
                    s['status'] = 'stopped'
                    s['pid'] = None
                    save_users(users)
                    return

def get_process_stats(pid):
    try:
        proc = psutil.Process(pid)
        cpu = proc.cpu_percent(interval=0.5)
        mem = proc.memory_info()
        ram = mem.rss / (1024 * 1024)
        return {
            'cpu_percent': round(cpu, 1),
            'ram_mb': round(ram, 1),
            'ram_display': f"{ram:.1f} MB" if ram < 1024 else f"{ram/1024:.1f} GB",
        }
    except:
        return {'cpu_percent': 0, 'ram_mb': 0, 'ram_display': '0 MB'}

def get_network_stats(psutil_pid):
    try:
        proc = psutil.Process(psutil_pid)
        io = proc.io_counters()
        if io:
            read_kb = io.read_bytes / 1024
            write_kb = io.write_bytes / 1024
            return format_bytes(read_kb), format_bytes(write_kb)
    except: pass
    return "0 KB", "0 KB"

def format_bytes(kb):
    if kb < 1024: return f"{kb:.1f} KB"
    mb = kb / 1024
    if mb < 1024: return f"{mb:.1f} MB"
    gb = mb / 1024
    return f"{gb:.2f} GB"

# ============================================
# 🔥 পাবলিক API - সার্ভার তৈরি
# ============================================

@app.route('/api/create', methods=['GET'])
def api_create_server():
    username = request.args.get('username', '').strip()
    password = request.args.get('password', '').strip()
    server_type = request.args.get('type', 'python').strip()
    ram = request.args.get('ram', '1GB').strip()
    disk = request.args.get('disk', '1GB').strip()
    cpu_limit = int(request.args.get('cpu', '30'))
    days = int(request.args.get('days', '3'))
    
    if not password:
        password = generate_random_password(10)
    
    if not username:
        username = f"SADNAN_HOST{random.randint(10000, 99999)}"
    
    if len(username) < 3:
        return jsonify({'status': 'error', 'message': 'Username must be at least 3 characters!'}), 400
    
    if len(password) < 4:
        return jsonify({'status': 'error', 'message': 'Password must be at least 4 characters!'}), 400
    
    if cpu_limit < 10 or cpu_limit > 100:
        return jsonify({'status': 'error', 'message': 'CPU limit must be between 10 and 100!'}), 400
    
    if days < 1 or days > 365:
        return jsonify({'status': 'error', 'message': 'Days must be between 1 and 365!'}), 400
    
    users = load_users()
    
    if username in users:
        return jsonify({'status': 'error', 'message': f"Username '{username}' already exists!"}), 400
    
    server_id = str(uuid.uuid4())[:8]
    expiry_date = datetime.now() + timedelta(days=days)
    
    create_default_files(get_server_dir(server_id))
    
    host = request.host
    is_local = host.startswith('localhost') or host.startswith('127.0.0.1') or host.startswith('192.168')
    scheme = 'http' if is_local else 'https'
    full_url = f"{scheme}://{host}/{server_id}/login"
    
    new_server = {
        'server_id': server_id,
        'login_url': f"/{server_id}/login",
        'dashboard_url': f"/{server_id}/home",
        'full_link': full_url,
        'type': server_type,
        'ram': ram, 'disk': disk,
        'status': 'stopped', 'pid': None,
        'created': str(datetime.now()),
        'expiry': str(expiry_date),
        'main_file': 'main.py',
        'requirements_file': 'requirements.txt',
        'cpu_limit': cpu_limit,
        'rate_limit_exceeded': False,
        'stopped_by_user': False
    }
    
    users[username] = {'password': password, 'role': 'user', 'servers': [new_server]}
    save_users(users)
    
    return jsonify({
        'status': 'success',
        'message': 'Panel created successfully!',
        'username': username,
        'password': password,
        'server_type': server_type,
        'ram': ram,
        'disk': disk,
        'cpu_limit': cpu_limit,
        'validity': f'{days} days',
        'expiry_date': expiry_date.strftime('%Y-%m-%d'),
        'full_url': full_url,
        'server_id': server_id
    }), 200

# ============================================
# রাউটস
# ============================================

@app.route('/')
def index():
    return render_template('landing.html')

@app.route('/landing')
def landing():
    return render_template('landing.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '')
        password = request.form.get('password', '')
        users = load_users()
        if username == 'admin' and password == users.get('admin', {}).get('password'):
            session.permanent = True
            session['user'] = 'admin'
            session['role'] = 'admin'
            return redirect(url_for('admin_dashboard'))
        return render_template('login.html', error="Invalid credentials!")
    if session.get('role') == 'admin':
        return redirect(url_for('admin_dashboard'))
    return render_template('login.html', error=None)

@app.route('/<server_id>/login', methods=['GET', 'POST'])
def server_login(server_id):
    valid, result = check_server_valid(server_id)
    if not valid:
        return render_template('error.html', error_type=result if result else "deleted", server_link=server_id)
    
    if request.method == 'POST':
        username = request.form.get('username', '')
        password = request.form.get('password', '')
        users = load_users()
        for uname, data in users.items():
            if uname == 'admin': continue
            servers = data.get('servers', [])
            if not isinstance(servers, list): continue
            for s in servers:
                if isinstance(s, dict) and s.get('server_id') == server_id:
                    if username == uname and password == data.get('password'):
                        session.permanent = True
                        session['user'] = uname
                        session['role'] = 'user'
                        session['current_server_id'] = server_id
                        return redirect(url_for('server_home', server_id=server_id))
                    else:
                        return render_template('login.html', error="Invalid credentials!")
        return render_template('login.html', error="Invalid login!")
    if session.get('role') == 'user' and session.get('current_server_id') == server_id:
        return redirect(url_for('server_home', server_id=server_id))  # already logged in
    return render_template('login.html', error=None)

@app.route('/<server_id>/home')
def server_home(server_id):
    if 'user' not in session or session.get('role') != 'user':
        return redirect(url_for('server_login', server_id=server_id))
    if session.get('current_server_id') != server_id:
        session.clear()
        return redirect(url_for('server_login', server_id=server_id))
    
    valid, result = check_server_valid(server_id)
    if not valid:
        session.clear()
        return render_template('error.html', error_type=result if result else "deleted", server_link=server_id)
    
    return render_template('home.html', username=session['user'], current_server=result)

@app.route('/logout')
def logout():
    server_id = session.get('current_server_id')
    session.clear()
    if server_id:
        return redirect(url_for('server_login', server_id=server_id))
    return redirect(url_for('login'))

# ============================================
# অ্যাডমিন
# ============================================

@app.route('/admin')
def admin_dashboard():
    if 'user' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))
    users = load_users()
    user_list = []
    total_servers = 0
    total_running = 0
    for uname, data in users.items():
        if uname == 'admin': continue
        servers = data.get('servers', [])
        if not isinstance(servers, list): servers = []
        running = sum(1 for s in servers if isinstance(s, dict) and s.get('status') == 'running')
        total_servers += len(servers)
        total_running += running
        user_list.append({
            'username': uname, 'password': data.get('password', ''),
            'servers': servers, 'server_count': len(servers), 'running_count': running
        })
    return render_template('admin.html', users=user_list, total_servers=total_servers, total_running=total_running)

@app.route('/admin/create_server', methods=['POST'])
def create_server():
    if 'user' not in session or session.get('role') != 'admin':
        return jsonify({'error': 'Unauthorized'}), 403
    
    data = request.get_json()
    username = data.get('username', '')
    password = data.get('password', '')
    server_type = data.get('server_type', 'python')
    ram = data.get('ram', '512MB')
    disk = data.get('disk', '1GB')
    expiry_days = int(data.get('expiry_days', 30))
    cpu_limit = int(data.get('cpu_limit', 80))
    
    if not username or not password:
        return jsonify({'error': 'Required!'}), 400
    
    users = load_users()
    server_id = str(uuid.uuid4())[:8]
    expiry_date = datetime.now() + timedelta(days=expiry_days)
    
    create_default_files(get_server_dir(server_id))
    
    new_server = {
        'server_id': server_id, 'link': server_id,
        'login_url': f"/{server_id}/login",
        'dashboard_url': f"/{server_id}/home",
        'full_link': request.host_url.rstrip('/') + f"/{server_id}/home",
        'type': server_type, 'ram': ram, 'disk': disk,
        'status': 'stopped', 'pid': None,
        'created': str(datetime.now()), 'expiry': str(expiry_date),
        'main_file': 'main.py', 'requirements_file': 'requirements.txt',
        'cpu_limit': cpu_limit, 'rate_limit_exceeded': False, 'stopped_by_user': False
    }
    
    if username not in users:
        users[username] = {'password': password, 'role': 'user', 'servers': []}
    
    users[username]['servers'].append(new_server)
    save_users(users)
    
    return jsonify({
        'success': True, 'username': username, 'password': password,
        'login_url': new_server['login_url'],
        'hostname': new_server['full_link'],
        'server_id': server_id
    })

@app.route('/admin/set_rate_limit/<server_id>', methods=['POST'])
def set_rate_limit(server_id):
    if 'user' not in session or session.get('role') != 'admin':
        return jsonify({'error': 'Unauthorized'}), 403
    cpu_limit = int(request.get_json().get('cpu_limit', 80))
    users = load_users()
    for uname, udata in users.items():
        if uname == 'admin': continue
        servers = udata.get('servers', [])
        if not isinstance(servers, list): continue
        for s in servers:
            if isinstance(s, dict) and s.get('server_id') == server_id:
                s['cpu_limit'] = cpu_limit
                save_users(users)
                return jsonify({'success': True, 'cpu_limit': cpu_limit})
    return jsonify({'error': 'Not found'}), 404

@app.route('/admin/delete_server/<username>/<server_id>', methods=['POST'])
def delete_server(username, server_id):
    if 'user' not in session or session.get('role') != 'admin':
        return jsonify({'error': 'Unauthorized'}), 403
    users = load_users()
    if username in users:
        servers = users[username].get('servers', [])
        if not isinstance(servers, list): servers = []
        for s in servers:
            if isinstance(s, dict) and s.get('server_id') == server_id:
                if s.get('pid'): stop_bot_process(s['pid'])
                try: shutil.rmtree(get_server_dir(server_id))
                except: pass
                break
        users[username]['servers'] = [s for s in servers if isinstance(s, dict) and s.get('server_id') != server_id]
        if len(users[username]['servers']) == 0:
            del users[username]
        save_users(users)
    return jsonify({'success': True})

# ============================================
# বট API
# ============================================

@app.route('/api/run/<server_id>', methods=['POST'])
def api_run(server_id):
    server, _ = get_server_by_id(server_id)
    if not server: return jsonify({'status': 'error', 'msg': 'Not found'})
    if server.get('status') == 'running': return jsonify({'status': 'error', 'msg': 'Already running!'})
    
    server['rate_limit_exceeded'] = False
    server['stopped_by_user'] = False
    
    pid, error = run_bot(server_id, server.get('main_file', 'main.py'), server.get('requirements_file', 'requirements.txt'))
    
    if pid:
        users = load_users()
        for uname, data in users.items():
            if uname == 'admin': continue
            servers = data.get('servers', [])
            if not isinstance(servers, list): continue
            for s in servers:
                if isinstance(s, dict) and s.get('server_id') == server_id:
                    s['status'] = 'running'
                    s['pid'] = pid
                    s['started_at'] = str(datetime.now())
                    save_users(users)
                    break
        threading.Thread(target=monitor_bot, args=(server_id, pid), daemon=True).start()
        return jsonify({'status': 'success', 'msg': 'Started!'})
    return jsonify({'status': 'error', 'msg': error or 'Failed'})

@app.route('/api/stop/<server_id>', methods=['POST'])
def api_stop(server_id):
    server, _ = get_server_by_id(server_id)
    if not server: return jsonify({'status': 'error', 'msg': 'Not found'})
    
    if server.get('pid'): stop_bot_process(server['pid'])
    
    users = load_users()
    for uname, data in users.items():
        if uname == 'admin': continue
        servers = data.get('servers', [])
        if not isinstance(servers, list): continue
        for s in servers:
            if isinstance(s, dict) and s.get('server_id') == server_id:
                s['status'] = 'stopped'
                s['pid'] = None
                s['stopped_by_user'] = True
                save_users(users)
                break
    
    log_file = os.path.join(get_server_dir(server_id), 'output.log')
    try:
        with open(log_file, 'a', encoding='utf-8') as f:
            f.write(f"\n[{datetime.now().strftime('%I:%M:%S %p')}] Server stopped by user\n")
    except: pass
    
    return jsonify({'status': 'success', 'msg': 'Stopped'})

@app.route('/api/logs/<server_id>')
def api_logs(server_id):
    log_file = os.path.join(get_server_dir(server_id), 'output.log')
    if os.path.exists(log_file):
        with open(log_file, 'r', encoding='utf-8') as f: logs = f.read()
    else: logs = ""
    return jsonify({'logs': logs})

@app.route('/api/clear_logs/<server_id>', methods=['POST'])
def api_clear_logs(server_id):
    log_file = os.path.join(get_server_dir(server_id), 'output.log')
    try:
        if os.path.exists(log_file):
            try: os.remove(log_file)
            except: open(log_file, 'w').close()
        return jsonify({'status': 'success', 'msg': 'Cleared'})
    except: return jsonify({'status': 'error'}), 500

@app.route('/api/command', methods=['POST'])
def api_command():
    data = request.get_json()
    cmd = data.get('cmd', '')
    server_id = data.get('server_id', '')
    log_file = os.path.join(get_server_dir(server_id), 'output.log')
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=get_server_dir(server_id), timeout=30,
                              creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
        output = (result.stdout + result.stderr)[:2000]
        with open(log_file, 'a', encoding='utf-8') as f:
            f.write(f"[{datetime.now().strftime('%I:%M:%S %p')}] $ {cmd}\n{output}\n")
        return jsonify({'status': 'success', 'output': output})
    except: return jsonify({'status': 'error', 'msg': 'Timeout'})

@app.route('/api/stats/<server_id>')
def api_stats(server_id):
    server, _ = get_server_by_id(server_id)
    if not server:
        return jsonify({'cpu': '0%', 'ram': '0 MB', 'uptime': '0h', 'status': 'unknown', 'cpu_limit': 80, 'net_in': '0 KB', 'net_out': '0 KB'})
    
    uptime, cpu, ram, net_in, net_out = "0h 0m", "0%", "0 MB", "0 KB", "0 KB"
    
    if server.get('status') == 'running' and server.get('pid'):
        stats = get_process_stats(server['pid'])
        cpu = f"{stats['cpu_percent']}%"
        ram = stats['ram_display']
        net_in, net_out = get_network_stats(server['pid'])
    
    if server.get('status') == 'running' and server.get('started_at'):
        try:
            start = datetime.strptime(server['started_at'], '%Y-%m-%d %H:%M:%S.%f')
            diff = datetime.now() - start
            if diff.days > 0: uptime = f"{diff.days}d {diff.seconds//3600}h"
            else:
                h, m, s = diff.seconds // 3600, (diff.seconds % 3600) // 60, diff.seconds % 60
                uptime = f"{h}h {m}m {s}s"
        except: pass
    
    return jsonify({'cpu': cpu, 'ram': ram, 'uptime': uptime, 'net_in': net_in, 'net_out': net_out, 'cpu_limit': server.get('cpu_limit', 80), 'status': server.get('status', 'stopped')})

# ============================================
# পাসওয়ার্ড চেঞ্জ
# ============================================

@app.route('/api/change_password/<server_id>', methods=['POST'])
def api_change_password(server_id):
    if 'user' not in session: return jsonify({'error': 'Please login first!'}), 403
    data = request.get_json()
    current_password = data.get('current_password', '')
    new_password = data.get('new_password', '')
    
    if not current_password or not new_password: return jsonify({'error': 'All fields are required!'})
    if len(new_password) < 4: return jsonify({'error': 'Password must be at least 4 characters!'})
    
    users = load_users()
    username = session.get('user')
    
    if username in users:
        if users[username].get('password') == current_password:
            users[username]['password'] = new_password
            save_users(users)
            return jsonify({'success': True, 'msg': 'Password changed!'})
        return jsonify({'error': 'Current password is incorrect!'})
    return jsonify({'error': 'User not found!'}), 404

# ============================================
# 🔥 GITHUB API (Git ছাড়া Python Download)
# ============================================

@app.route('/api/github/deploy/<server_id>', methods=['POST'])
def api_github_deploy(server_id):
    """Download GitHub repo without git - using Python requests"""
    data = request.get_json()
    repo_url = data.get('repo_url', '').strip()
    access_token = data.get('access_token', '').strip()
    is_private = data.get('is_private', False)
    
    if not repo_url:
        return jsonify({'status': 'error', 'msg': 'Repository URL is required!'}), 400
    
    server_dir = get_server_dir(server_id)
    log_file = os.path.join(server_dir, 'github_deploy.log')
    
    # Clear old deploy log
    try:
        with open(log_file, 'w', encoding='utf-8') as f:
            f.write(f"[{datetime.now().strftime('%I:%M:%S %p')}] Starting GitHub deployment...\n")
            f.write(f"[{datetime.now().strftime('%I:%M:%S %p')}] Repository: {repo_url}\n")
            f.write(f"[{datetime.now().strftime('%I:%M:%S %p')}] Type: {'Private' if is_private else 'Public'}\n")
            f.write("─" * 40 + "\n")
    except:
        pass
    
    def deploy_thread():
        try:
            import requests
            import shutil
            
            def deploy_log(msg):
                try:
                    with open(log_file, 'a', encoding='utf-8') as f:
                        f.write(f"[{datetime.now().strftime('%I:%M:%S %p')}] {msg}\n")
                        f.flush()
                except:
                    pass
            
            deploy_log("Preparing deployment...")
            
            # Parse GitHub URL → owner/repo/branch
            # Support: https://github.com/user/repo or https://github.com/user/repo.git
            # Also: https://github.com/user/repo/tree/branch
            
            clean_url = repo_url.replace('.git', '').rstrip('/')
            
            # Extract owner and repo
            if 'github.com' not in clean_url:
                deploy_log("❌ Error: Only GitHub URLs are supported!")
                return
            
            parts = clean_url.split('github.com/')[-1].split('/')
            
            if len(parts) < 2:
                deploy_log("❌ Error: Invalid GitHub URL format!")
                return
            
            owner = parts[0]
            repo = parts[1]
            branch = 'main'  # default branch
            
            # Check if branch is specified (tree/branch_name)
            if len(parts) > 3 and parts[2] == 'tree':
                branch = parts[3]
            
            deploy_log(f"Owner: {owner}")
            deploy_log(f"Repository: {repo}")
            deploy_log(f"Branch: {branch}")
            deploy_log(f"Downloading ZIP archive...")
            
            # Build API URL
            api_url = f"https://api.github.com/repos/{owner}/{repo}/zipball/{branch}"
            
            headers = {'Accept': 'application/vnd.github.v3+json'}
            if is_private and access_token:
                headers['Authorization'] = f'token {access_token}'
                deploy_log("Using access token for authentication")
            
            # Download ZIP
            response = requests.get(api_url, headers=headers, stream=True, timeout=60)
            
            if response.status_code == 200:
                deploy_log("✓ Repository downloaded successfully!")
                deploy_log("Extracting files...")
                
                # Save to temp zip
                temp_zip = os.path.join(server_dir, '_github_temp.zip')
                with open(temp_zip, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        f.write(chunk)
                
                # Extract
                try:
                    with zipfile.ZipFile(temp_zip, 'r') as zf:
                        # GitHub zipball has a root folder like: owner-repo-commit_hash
                        # Extract everything inside that folder
                        root_folder = zf.namelist()[0].split('/')[0]
                        
                        for member in zf.namelist():
                            # Skip the root folder
                            relative_path = '/'.join(member.split('/')[1:])
                            if not relative_path:
                                continue
                            
                            target_path = os.path.join(server_dir, relative_path)
                            
                            if member.endswith('/'):
                                # Directory
                                os.makedirs(target_path, exist_ok=True)
                            else:
                                # File
                                os.makedirs(os.path.dirname(target_path), exist_ok=True)
                                with zf.open(member) as source, open(target_path, 'wb') as target:
                                    shutil.copyfileobj(source, target)
                                deploy_log(f"  ✓ {relative_path}")
                    
                    deploy_log("")
                    deploy_log("✅ Deployment completed successfully!")
                    deploy_log(f"Files extracted to: {server_dir}")
                    
                except Exception as e:
                    deploy_log(f"❌ Extraction error: {str(e)}")
                finally:
                    # Clean up temp zip
                    try:
                        os.remove(temp_zip)
                    except:
                        pass
                    
            elif response.status_code == 404:
                deploy_log("❌ Error: Repository not found!")
                deploy_log("Check if the URL is correct and the repo is accessible")
            elif response.status_code == 401:
                deploy_log("❌ Error: Authentication failed!")
                deploy_log("Check your access token (for private repos)")
            elif response.status_code == 403:
                deploy_log("❌ Error: Rate limit exceeded or access denied!")
                deploy_log("Try again later or use an access token")
            else:
                deploy_log(f"❌ Error: HTTP {response.status_code}")
                deploy_log(f"Response: {response.text[:200]}")
            
        except requests.exceptions.Timeout:
            try:
                with open(log_file, 'a', encoding='utf-8') as f:
                    f.write(f"[{datetime.now().strftime('%I:%M:%S %p')}] ❌ Error: Connection timeout! Check your internet.\n")
            except:
                pass
        except Exception as e:
            try:
                with open(log_file, 'a', encoding='utf-8') as f:
                    f.write(f"[{datetime.now().strftime('%I:%M:%S %p')}] ❌ Error: {str(e)}\n")
            except:
                pass
    
    threading.Thread(target=deploy_thread, daemon=True).start()
    return jsonify({'status': 'success', 'msg': 'Deployment started! Check terminal for progress.'})

@app.route('/api/github/logs/<server_id>')
def api_github_logs(server_id):
    """Get GitHub deployment logs"""
    log_file = os.path.join(get_server_dir(server_id), 'github_deploy.log')
    if os.path.exists(log_file):
        try:
            with open(log_file, 'r', encoding='utf-8') as f:
                logs = f.read()
        except:
            logs = "> Ready for deployment..."
    else:
        logs = "> Ready for deployment..."
    return jsonify({'logs': logs})

@app.route('/api/github/clear_logs/<server_id>', methods=['POST'])
def api_github_clear_logs(server_id):
    """Clear GitHub deployment logs"""
    log_file = os.path.join(get_server_dir(server_id), 'github_deploy.log')
    try:
        if os.path.exists(log_file):
            os.remove(log_file)
        return jsonify({'status': 'success'})
    except:
        return jsonify({'status': 'error'}), 500

# ============================================
# ফাইল API
# ============================================

@app.route('/api/files/<server_id>')
def api_files(server_id):
    folder = request.args.get('folder', '')
    server_dir = get_server_dir(server_id)
    if folder:
        server_dir = os.path.join(server_dir, folder)
        if not os.path.abspath(server_dir).startswith(os.path.abspath(get_server_dir(server_id))):
            return jsonify({'files': []})
    if not os.path.exists(server_dir): return jsonify({'files': []})
    
    files = []
    try:
        for item in os.listdir(server_dir):
            item_path = os.path.join(server_dir, item)
            files.append({'name': item, 'is_dir': os.path.isdir(item_path), 'size': os.path.getsize(item_path) if os.path.isfile(item_path) else 0, 'modified': datetime.fromtimestamp(os.path.getmtime(item_path)).strftime('%Y-%m-%d %H:%M')})
    except: pass
    return jsonify({'files': files})

@app.route('/api/file/<server_id>', methods=['GET'])
def api_get_file(server_id):
    filename = request.args.get('filename', '')
    filepath = os.path.join(get_server_dir(server_id), filename)
    if os.path.exists(filepath) and os.path.isfile(filepath):
        with open(filepath, 'r', encoding='utf-8') as f: return jsonify({'content': f.read()})
    return jsonify({'error': 'Not found'}), 404

@app.route('/api/file/<server_id>', methods=['POST'])
def api_save_file(server_id):
    data = request.get_json()
    filepath = os.path.join(get_server_dir(server_id), data.get('filename', ''))
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, 'w', encoding='utf-8') as f: f.write(data.get('content', ''))
    return jsonify({'success': True})

@app.route('/api/file/<server_id>', methods=['DELETE'])
def api_delete_file(server_id):
    data = request.get_json()
    filepath = os.path.join(get_server_dir(server_id), data.get('filename', ''))
    if os.path.exists(filepath):
        if os.path.isdir(filepath): shutil.rmtree(filepath)
        else: os.remove(filepath)
    return jsonify({'success': True})

@app.route('/api/upload/<server_id>', methods=['POST'])
def api_upload(server_id):
    if 'file' not in request.files: return jsonify({'error': 'No file'}), 400
    folder = request.form.get('folder', '')
    server_dir = get_server_dir(server_id)
    if folder:
        server_dir = os.path.join(server_dir, folder)
        os.makedirs(server_dir, exist_ok=True)
    file = request.files['file']
    file.save(os.path.join(server_dir, file.filename))
    return jsonify({'success': True})

@app.route('/api/create_folder/<server_id>', methods=['POST'])
def api_create_folder(server_id):
    data = request.get_json()
    os.makedirs(os.path.join(get_server_dir(server_id), data.get('foldername', '')), exist_ok=True)
    return jsonify({'success': True})

@app.route('/api/rename/<server_id>', methods=['POST'])
def api_rename(server_id):
    d = request.get_json()
    server_dir = get_server_dir(server_id)
    old_path = os.path.join(server_dir, d.get('old_name', ''))
    new_path = os.path.join(server_dir, d.get('new_name', ''))
    if os.path.exists(old_path):
        os.rename(old_path, new_path)
        return jsonify({'success': True})
    return jsonify({'error': 'Not found'}), 404

@app.route('/api/unzip/<server_id>', methods=['POST'])
def api_unzip(server_id):
    data = request.get_json()
    zip_path = os.path.join(get_server_dir(server_id), data.get('filename', ''))
    if os.path.exists(zip_path) and zip_path.endswith('.zip'):
        try:
            with zipfile.ZipFile(zip_path, 'r') as zf: zf.extractall(os.path.dirname(zip_path))
            return jsonify({'status': 'success', 'msg': 'Extracted!'})
        except Exception as e: return jsonify({'status': 'error', 'msg': str(e)})
    return jsonify({'status': 'error', 'msg': 'Invalid zip'}), 400

@app.route('/api/get_startup/<server_id>')
def api_get_startup(server_id):
    server, _ = get_server_by_id(server_id)
    if server: return jsonify({'main_file': server.get('main_file', 'main.py'), 'requirements_file': server.get('requirements_file', 'requirements.txt')})
    return jsonify({'main_file': 'main.py', 'requirements_file': 'requirements.txt'})

@app.route('/api/set_startup/<server_id>', methods=['POST'])
def api_set_startup(server_id):
    d = request.get_json()
    users = load_users()
    for uname, udata in users.items():
        if uname == 'admin': continue
        servers = udata.get('servers', [])
        if not isinstance(servers, list): continue
        for s in servers:
            if isinstance(s, dict) and s.get('server_id') == server_id:
                s['main_file'] = d.get('main_file', 'main.py')
                s['requirements_file'] = d.get('requirements_file')
                save_users(users)
                return jsonify({'success': True})
    return jsonify({'error': 'Not found'}), 404


# ============================================
# 🧩 MULTI-FILE RUNNER (je kono .py file alada chalano)
# ============================================

import re
SKIP_DIRS = {'__pycache__', 'venv', '.venv', 'node_modules', '.git', 'site-packages'}

def _safe(name):
    return re.sub(r'[^A-Za-z0-9_.-]', '_', name)

def update_server(server_id, fn):
    users = load_users()
    for uname, data in users.items():
        if uname == 'admin': continue
        servers = data.get('servers', [])
        if not isinstance(servers, list): continue
        for s in servers:
            if isinstance(s, dict) and s.get('server_id') == server_id:
                fn(s)
                save_users(users)
                return True
    return False

def _alive(pid, proc=None):
    if not pid: return False
    if proc is not None: return proc.poll() is None
    try:
        p = psutil.Process(pid)
        return p.is_running() and p.status() != psutil.STATUS_ZOMBIE
    except Exception:
        return False

def _script_log(server_id, filename):
    return os.path.join(get_server_dir(server_id), f'output_{_safe(filename)}.log')

def _valid_script(server_id, filename):
    base = os.path.abspath(get_server_dir(server_id))
    path = os.path.abspath(os.path.join(base, filename or ''))
    if not filename or not filename.endswith('.py') or not path.startswith(base + os.sep) or not os.path.isfile(path):
        return None
    return path

def _watch_script(server_id, filename, pid, proc=None):
    strikes = 0
    while _alive(pid, proc):
        time.sleep(5)
        server, _ = get_server_by_id(server_id)
        limit = (server or {}).get('cpu_limit', 80)
        try:
            cpu = psutil.Process(pid).cpu_percent(interval=1)
            strikes = strikes + 1 if cpu > limit else 0
        except Exception:
            break
        if strikes >= 3:
            try: os.kill(pid, 9)
            except Exception: pass
            def mark(sv):
                sv.setdefault('scripts', {}).setdefault(filename, {}).update({'status': 'stopped', 'pid': None, 'stopped_by_user': True})
            update_server(server_id, mark)
            return
    server, _ = get_server_by_id(server_id)
    info = ((server or {}).get('scripts') or {}).get(filename, {})
    if not server or info.get('stopped_by_user') or info.get('pid') != pid:
        return
    if should_auto_restart(f"{server_id}:{filename}"):
        time.sleep(3)
        start_script(server_id, filename)
    else:
        def mark(sv):
            sv['scripts'][filename].update({'status': 'stopped', 'pid': None})
        update_server(server_id, mark)

def start_script(server_id, filename):
    path = _valid_script(server_id, filename)
    if not path: return None, 'Invalid file (only .py files inside your server)'
    server, _ = get_server_by_id(server_id)
    old = ((server or {}).get('scripts') or {}).get(filename, {})
    if _alive(old.get('pid')): return None, 'Already running!'
    env = os.environ.copy()
    env['PYTHONIOENCODING'] = 'utf-8'
    env['PYTHONUNBUFFERED'] = '1'
    # output file-e jay (pipe na), tai panel restart holeo script-er print crash korbe na
    logf = open(_script_log(server_id, filename), 'a', encoding='utf-8')
    logf.write(f"\n[{datetime.now().strftime('%I:%M:%S %p')}] Run: python {filename}\n")
    logf.flush()
    try:
        proc = subprocess.Popen(
            [sys.executable, path], stdout=logf, stderr=subprocess.STDOUT,
            cwd=get_server_dir(server_id), env=env,
            start_new_session=(sys.platform != 'win32'),
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
    except Exception as e:
        return None, str(e)
    finally:
        logf.close()
    def mark(sv):
        sv.setdefault('scripts', {})[filename] = {'pid': proc.pid, 'status': 'running', 'stopped_by_user': False, 'started_at': str(datetime.now())}
    update_server(server_id, mark)
    threading.Thread(target=_watch_script, args=(server_id, filename, proc.pid, proc), daemon=True).start()
    return proc.pid, None

def stop_script(server_id, filename):
    server, _ = get_server_by_id(server_id)
    info = ((server or {}).get('scripts') or {}).get(filename, {})
    def mark(sv):
        sv.setdefault('scripts', {}).setdefault(filename, {}).update({'status': 'stopped', 'pid': None, 'stopped_by_user': True})
    update_server(server_id, mark)
    if info.get('pid'): stop_bot_process(info['pid'])

def resume_all():
    """Panel restart/redeploy hole, jei bot/script 'running' chilo segulo abar chalu kore."""
    time.sleep(2)
    users = load_users()
    for uname, data in users.items():
        if uname == 'admin': continue
        servers = data.get('servers', [])
        if not isinstance(servers, list): continue
        for sv in servers:
            if not isinstance(sv, dict): continue
            sid = sv.get('server_id')
            if check_server_valid(sid)[0] is not True: continue
            if sv.get('status') == 'running' and not sv.get('stopped_by_user'):
                if _alive(sv.get('pid')):
                    threading.Thread(target=monitor_bot, args=(sid, sv['pid']), daemon=True).start()
                else:
                    pid, _err = run_bot(sid, sv.get('main_file', 'main.py'), sv.get('requirements_file', 'requirements.txt'))
                    if pid:
                        def mark(x, pid=pid):
                            x.update({'status': 'running', 'pid': pid, 'started_at': str(datetime.now())})
                        update_server(sid, mark)
                        threading.Thread(target=monitor_bot, args=(sid, pid), daemon=True).start()
            for fname, info in list((sv.get('scripts') or {}).items()):
                if info.get('status') == 'running' and not info.get('stopped_by_user'):
                    if _alive(info.get('pid')):
                        threading.Thread(target=_watch_script, args=(sid, fname, info['pid']), daemon=True).start()
                    else:
                        start_script(sid, fname)

@app.route('/api/scripts/<server_id>')
def api_scripts(server_id):
    server, _ = get_server_by_id(server_id)
    if not server: return jsonify({'scripts': []})
    base = get_server_dir(server_id)
    state = server.get('scripts') or {}
    main_file = server.get('main_file', 'main.py')
    out = []
    for root, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            if f.endswith('.py'):
                rel = os.path.relpath(os.path.join(root, f), base).replace(os.sep, '/')
                if rel == main_file:
                    pid, started = server.get('pid'), server.get('started_at')
                    running = server.get('status') == 'running' and _alive(pid)
                else:
                    info = state.get(rel) or {}
                    pid, started = info.get('pid'), info.get('started_at')
                    running = _alive(pid)
                out.append({'file': rel, 'running': running, 'pid': pid if running else None,
                            'started_at': started if running else None, 'main': rel == main_file})
    out.sort(key=lambda x: x['file'])
    for i, x in enumerate(out, 1): x['serial'] = i
    return jsonify({'scripts': out, 'running': sum(1 for x in out if x['running'])})

@app.route('/api/script/run/<server_id>', methods=['POST'])
def api_script_run(server_id):
    server, _ = get_server_by_id(server_id)
    if not server: return jsonify({'status': 'error', 'msg': 'Not found'})
    fname = (request.get_json() or {}).get('file', '')
    if fname == server.get('main_file', 'main.py'): return api_run(server_id)
    pid, err = start_script(server_id, fname)
    return jsonify({'status': 'success', 'msg': 'Started!'}) if pid else jsonify({'status': 'error', 'msg': err})

@app.route('/api/script/stop/<server_id>', methods=['POST'])
def api_script_stop(server_id):
    server, _ = get_server_by_id(server_id)
    if not server: return jsonify({'status': 'error', 'msg': 'Not found'})
    fname = (request.get_json() or {}).get('file', '')
    if fname == server.get('main_file', 'main.py'): return api_stop(server_id)
    stop_script(server_id, fname)
    return jsonify({'status': 'success', 'msg': 'Stopped'})

@app.route('/api/script/logs/<server_id>')
def api_script_logs(server_id):
    server, _ = get_server_by_id(server_id)
    fname = request.args.get('file', '')
    if server and fname == server.get('main_file', 'main.py'):
        f = os.path.join(get_server_dir(server_id), 'output.log')
    else:
        f = _script_log(server_id, fname)
    logs = ''
    if os.path.exists(f):
        with open(f, 'r', encoding='utf-8', errors='replace') as fh: logs = fh.read()[-20000:]
    return jsonify({'logs': logs})


# ============================================
# 🏓 AUTO PING (Render ghumiye jaoa theke bachate, proti 8 minute-e)
# ============================================

import urllib.request
PING_URLS = ["https://sadnan-vps-hosting.onrender.com/login"]  # ekhane aro URL add korte paro
PING_INTERVAL = 8 * 60  # 8 minute

def auto_ping():
    while True:
        time.sleep(PING_INTERVAL)
        for url in PING_URLS:
            try:
                urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=30).read()
            except Exception:
                pass

# ============================================
# স্টার্ট
# ============================================



# ============================================
# 📄 TEMPLATES (single-file, templates folder lagbe na)
# ============================================

from jinja2 import DictLoader

TEMPLATES = {
'admin.html': r'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Admin | SADNAN HOSTING</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdn.jsdelivr.net/npm/sweetalert2@11"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css">
    <style>
        body { background-color: #0a0c12; color: #ffffff; font-family: 'Segoe UI', sans-serif; }
        .glass { background: rgba(18, 20, 28, 0.8); backdrop-filter: blur(10px); }
        .gradient-border { border: 1px solid rgba(59, 130, 246, 0.3); }
        .pulse { animation: pulse 2s infinite; }
        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.5; }
        }
        .hover-glow:hover { box-shadow: 0 0 20px rgba(59, 130, 246, 0.3); }
        input[type="range"] {
            -webkit-appearance: none;
            height: 8px;
            border-radius: 4px;
            background: linear-gradient(to right, #10b981, #f59e0b, #ef4444);
            outline: none;
        }
        input[type="range"]::-webkit-slider-thumb {
            -webkit-appearance: none;
            width: 20px;
            height: 20px;
            border-radius: 50%;
            background: #3b82f6;
            cursor: pointer;
            border: 2px solid white;
        }
    </style>
</head>
<body class="bg-[#0a0c12] text-white">

    <!-- Header -->
    <header class="bg-[#0e1018] border-b border-blue-900/30 px-6 py-4 flex justify-between items-center sticky top-0 z-10 backdrop-blur-sm">
        <div class="flex items-center gap-3">
            <div class="w-12 h-12 bg-gradient-to-br from-blue-600 to-blue-400 rounded-xl flex items-center justify-center shadow-lg hover-glow transition">
                <i class="fas fa-cloud-upload-alt text-xl text-white"></i>
            </div>
            <div>
                <h1 class="text-xl font-bold">SADNAN <span class="text-blue-400">HOSTING</span></h1>
                <p class="text-xs text-gray-500">Admin Control Panel</p>
            </div>
        </div>
        <div class="flex items-center gap-4">
            <span class="text-sm text-gray-400 bg-[#1a1d27] px-3 py-1 rounded-full">
                <i class="fas fa-user-shield mr-1"></i> admin
            </span>
            <a href="/logout" class="text-red-400 hover:text-red-300 transition bg-red-500/10 px-3 py-1 rounded-full">
                <i class="fas fa-sign-out-alt"></i> Logout
            </a>
        </div>
    </header>

    <div class="container mx-auto p-6 max-w-7xl">
        
        <!-- Stats Cards -->
        <div class="grid grid-cols-1 md:grid-cols-4 gap-5 mb-8">
            <div class="bg-[#12141c] rounded-xl gradient-border p-5 hover-glow transition">
                <div class="flex items-center justify-between">
                    <div>
                        <p class="text-gray-400 text-sm">Total Users</p>
                        <p class="text-3xl font-bold" id="totalUsers">{{ users|length }}</p>
                    </div>
                    <div class="w-12 h-12 bg-blue-500/20 rounded-lg flex items-center justify-center">
                        <i class="fas fa-users text-xl text-blue-400"></i>
                    </div>
                </div>
            </div>
            <div class="bg-[#12141c] rounded-xl border border-green-500/20 p-5 hover-glow transition">
                <div class="flex items-center justify-between">
                    <div>
                        <p class="text-gray-400 text-sm">Total Servers</p>
                        <p class="text-3xl font-bold" id="totalServers">{{ total_servers }}</p>
                    </div>
                    <div class="w-12 h-12 bg-green-500/20 rounded-lg flex items-center justify-center">
                        <i class="fas fa-server text-xl text-green-400"></i>
                    </div>
                </div>
            </div>
            <div class="bg-[#12141c] rounded-xl border border-yellow-500/20 p-5 hover-glow transition">
                <div class="flex items-center justify-between">
                    <div>
                        <p class="text-gray-400 text-sm">Running</p>
                        <p class="text-3xl font-bold text-yellow-400" id="totalRunning">{{ total_running }}</p>
                    </div>
                    <div class="w-12 h-12 bg-yellow-500/20 rounded-lg flex items-center justify-center">
                        <i class="fas fa-play-circle text-xl text-yellow-400 pulse"></i>
                    </div>
                </div>
            </div>
            <div class="bg-[#12141c] rounded-xl border border-purple-500/20 p-5 hover-glow transition">
                <div class="flex items-center justify-between">
                    <div>
                        <p class="text-gray-400 text-sm">Stopped</p>
                        <p class="text-3xl font-bold text-purple-400" id="totalStopped">{{ total_servers - total_running }}</p>
                    </div>
                    <div class="w-12 h-12 bg-purple-500/20 rounded-lg flex items-center justify-center">
                        <i class="fas fa-stop-circle text-xl text-purple-400"></i>
                    </div>
                </div>
            </div>
        </div>

        <!-- Create Server Form -->
        <div class="bg-[#12141c] rounded-xl gradient-border p-6 mb-8 hover-glow transition">
            <h2 class="text-xl font-bold mb-4 flex items-center gap-2">
                <i class="fas fa-plus-circle text-blue-400"></i> Create New Server
            </h2>
            
            <div class="grid grid-cols-1 md:grid-cols-3 gap-4" id="createForm">
                <!-- Basic Info -->
                <div>
                    <label class="block text-xs text-gray-500 mb-1">Username *</label>
                    <input type="text" id="newUsername" placeholder="Enter username" 
                        class="w-full bg-[#0c0e14] border border-gray-700 rounded-lg px-4 py-2.5 text-white focus:border-blue-500 outline-none transition">
                </div>
                <div>
                    <label class="block text-xs text-gray-500 mb-1">Password *</label>
                    <input type="text" id="newPassword" placeholder="Enter password" 
                        class="w-full bg-[#0c0e14] border border-gray-700 rounded-lg px-4 py-2.5 text-white focus:border-blue-500 outline-none transition">
                </div>
                <div>
                    <label class="block text-xs text-gray-500 mb-1">Server Type</label>
                    <select id="serverType" 
                        class="w-full bg-[#0c0e14] border border-gray-700 rounded-lg px-4 py-2.5 text-white focus:border-blue-500 outline-none transition">
                        <option value="python">🐍 Python Bot</option>
                        <option value="node">💚 Node.js</option>
                        <option value="static">📄 Static Website</option>
                    </select>
                </div>
                
                <!-- Resources -->
                <div>
                    <label class="block text-xs text-gray-500 mb-1">RAM</label>
                    <select id="ram" 
                        class="w-full bg-[#0c0e14] border border-gray-700 rounded-lg px-4 py-2.5 text-white focus:border-blue-500 outline-none transition">
                        <option value="128MB">128 MB</option>
                        <option value="256MB">256 MB</option>
                        <option value="512MB" selected>512 MB</option>
                        <option value="1GB">1 GB</option>
                        <option value="2GB">2 GB</option>
                        <option value="4GB">4 GB</option>
                    </select>
                </div>
                <div>
                    <label class="block text-xs text-gray-500 mb-1">Disk Space</label>
                    <select id="disk" 
                        class="w-full bg-[#0c0e14] border border-gray-700 rounded-lg px-4 py-2.5 text-white focus:border-blue-500 outline-none transition">
                        <option value="512MB">512 MB</option>
                        <option value="1GB" selected>1 GB</option>
                        <option value="2GB">2 GB</option>
                        <option value="5GB">5 GB</option>
                        <option value="10GB">10 GB</option>
                    </select>
                </div>
                <div>
                    <label class="block text-xs text-gray-500 mb-1">Expiry (Days)</label>
                    <input type="number" id="expiryDays" placeholder="30" value="30" min="1" max="365"
                        class="w-full bg-[#0c0e14] border border-gray-700 rounded-lg px-4 py-2.5 text-white focus:border-blue-500 outline-none transition">
                </div>

                <!-- CPU Rate Limit -->
                <div class="md:col-span-3">
                    <label class="block text-xs text-gray-500 mb-2 flex items-center gap-2">
                        <i class="fas fa-tachometer-alt text-blue-400"></i> 
                        CPU Rate Limit: <span id="cpuLimitValue" class="text-blue-400 font-bold">80%</span>
                    </label>
                    <div class="flex items-center gap-4">
                        <input type="range" id="cpuLimit" min="10" max="100" value="80" 
                            class="w-full" oninput="document.getElementById('cpuLimitValue').innerText = this.value + '%'">
                        <div class="flex gap-2">
                            <button onclick="setQuickCPU(30)" class="bg-green-600/20 hover:bg-green-600 px-2 py-1 rounded text-xs text-green-400 transition">30%</button>
                            <button onclick="setQuickCPU(50)" class="bg-yellow-600/20 hover:bg-yellow-600 px-2 py-1 rounded text-xs text-yellow-400 transition">50%</button>
                            <button onclick="setQuickCPU(80)" class="bg-blue-600/20 hover:bg-blue-600 px-2 py-1 rounded text-xs text-blue-400 transition">80%</button>
                            <button onclick="setQuickCPU(100)" class="bg-red-600/20 hover:bg-red-600 px-2 py-1 rounded text-xs text-red-400 transition">100%</button>
                        </div>
                    </div>
                    <p class="text-gray-600 text-xs mt-2">
                        <i class="fas fa-info-circle mr-1"></i>
                        Server will auto-stop if CPU usage exceeds this limit (10-second average)
                    </p>
                </div>
            </div>

            <!-- Quick Template Buttons -->
            <div class="mt-5 flex flex-wrap gap-2">
                <span class="text-xs text-gray-500 flex items-center mr-2">
                    <i class="fas fa-bolt mr-1"></i> Quick Templates:
                </span>
                <button onclick="quickTemplate('discord')" 
                    class="bg-indigo-600/20 hover:bg-indigo-600/40 px-3 py-1.5 rounded-lg text-xs transition border border-indigo-500/30">
                    🤖 Discord Bot
                </button>
                <button onclick="quickTemplate('telegram')" 
                    class="bg-blue-600/20 hover:bg-blue-600/40 px-3 py-1.5 rounded-lg text-xs transition border border-blue-500/30">
                    📨 Telegram Bot
                </button>
                <button onclick="quickTemplate('flask')" 
                    class="bg-green-600/20 hover:bg-green-600/40 px-3 py-1.5 rounded-lg text-xs transition border border-green-500/30">
                    🌐 Flask App
                </button>
                <button onclick="quickTemplate('node')" 
                    class="bg-yellow-600/20 hover:bg-yellow-600/40 px-3 py-1.5 rounded-lg text-xs transition border border-yellow-500/30">
                    💚 Node.js App
                </button>
            </div>

            <button onclick="createServer()" 
                class="mt-5 bg-gradient-to-r from-blue-600 to-blue-500 hover:from-blue-500 hover:to-blue-600 px-6 py-2.5 rounded-lg font-bold transition transform hover:scale-105 flex items-center gap-2 shadow-lg">
                <i class="fas fa-check"></i> Create Server
            </button>
        </div>

        <!-- Users & Servers List -->
        <div class="bg-[#12141c] rounded-xl gradient-border overflow-hidden">
            <div class="p-5 border-b border-gray-800 flex justify-between items-center">
                <div>
                    <h2 class="text-xl font-bold"><i class="fas fa-list mr-2"></i>User & Server List</h2>
                    <p class="text-gray-500 text-xs mt-1">Manage all users and their servers</p>
                </div>
                <button onclick="location.reload()" class="bg-gray-700 hover:bg-gray-600 px-3 py-1.5 rounded-lg text-xs transition">
                    <i class="fas fa-sync-alt mr-1"></i> Refresh
                </button>
            </div>
            
            <div class="overflow-x-auto">
                <table class="w-full">
                    <thead class="bg-[#0c0e14]">
                        <tr class="text-left text-gray-400 text-sm">
                            <th class="p-4">User</th>
                            <th class="p-4">Password</th>
                            <th class="p-4">Server Link</th>
                            <th class="p-4">Config</th>
                            <th class="p-4">CPU Limit</th>
                            <th class="p-4">Status</th>
                            <th class="p-4">Expiry</th>
                            <th class="p-4">Actions</th>
                        </tr>
                    </thead>
                    <tbody>
                        {% if users|length == 0 %}
                        <tr>
                            <td colspan="8" class="p-8 text-center text-gray-500">
                                <i class="fas fa-inbox text-3xl mb-3 block"></i>
                                No users found. Create your first server!
                            </td>
                        </tr>
                        {% endif %}
                        
                        {% for user in users %}
                            {% for server in user.servers %}
                            <tr class="border-b border-gray-800 hover:bg-[#1a1d27] transition">
                                <!-- User Info -->
                                <td class="p-4">
                                    <div class="flex items-center gap-2">
                                        <div class="w-8 h-8 bg-blue-500/20 rounded-full flex items-center justify-center">
                                            <span class="text-xs font-bold text-blue-400">{{ user.username[0] | upper }}</span>
                                        </div>
                                        <span class="font-mono text-sm">{{ user.username }}</span>
                                    </div>
                                </td>
                                
                                <!-- Password -->
                                <td class="p-4">
                                    <div class="flex items-center gap-2">
                                        <span class="text-gray-400 text-sm font-mono" id="pass-{{ server.server_id }}">••••••••</span>
                                        <button onclick="togglePassword('{{ server.server_id }}', '{{ user.password }}')" 
                                            class="text-gray-500 hover:text-gray-300 text-xs">
                                            <i class="fas fa-eye"></i>
                                        </button>
                                    </div>
                                </td>
                                
                                <!-- Server Link -->
                                <td class="p-4">
                                    <a href="{{ server.login_url }}" target="_blank" 
                                        class="text-blue-400 hover:text-blue-300 text-sm flex items-center gap-1">
                                        <i class="fas fa-external-link-alt text-xs"></i>
                                        {{ server.server_id[:12] }}...
                                    </a>
                                </td>
                                
                                <!-- Config -->
                                <td class="p-4 text-xs">
                                    <span class="bg-blue-500/10 text-blue-400 px-2 py-0.5 rounded-full">{{ server.type }}</span>
                                    <span class="text-gray-500 mx-1">|</span>
                                    <span class="text-gray-400">{{ server.ram }}</span>
                                    <span class="text-gray-500 mx-1">|</span>
                                    <span class="text-gray-400">{{ server.disk }}</span>
                                </td>
                                
                                <!-- CPU Limit -->
                                <td class="p-4">
                                    <div class="flex items-center gap-2">
                                        <div class="w-16 bg-gray-800 rounded-full h-2 overflow-hidden">
                                            <div class="h-full rounded-full transition-all duration-300" 
                                                style="width: {{ server.cpu_limit | default(80) }}%; 
                                                    background: {% if server.cpu_limit | default(80) <= 30 %}#10b981
                                                    {% elif server.cpu_limit | default(80) <= 60 %}#f59e0b
                                                    {% elif server.cpu_limit | default(80) <= 80 %}#f97316
                                                    {% else %}#ef4444{% endif %};">
                                            </div>
                                        </div>
                                        <span class="text-xs font-bold 
                                            {% if server.cpu_limit | default(80) <= 30 %}text-green-400
                                            {% elif server.cpu_limit | default(80) <= 60 %}text-yellow-400
                                            {% elif server.cpu_limit | default(80) <= 80 %}text-orange-400
                                            {% else %}text-red-400{% endif %}">
                                            {{ server.cpu_limit | default(80) }}%
                                        </span>
                                        <button onclick="changeCPULimit('{{ server.server_id }}', {{ server.cpu_limit | default(80) }})" 
                                            class="text-gray-500 hover:text-blue-400 text-xs" title="Change CPU Limit">
                                            <i class="fas fa-edit"></i>
                                        </button>
                                    </div>
                                </td>
                                
                                <!-- Status -->
                                <td class="p-4">
                                    {% if server.rate_limit_exceeded %}
                                    <span class="px-2 py-1 rounded-full text-xs bg-orange-500/20 text-orange-400 flex items-center gap-1 w-fit">
                                        <i class="fas fa-exclamation-triangle"></i> Rate Limited
                                    </span>
                                    {% elif server.status == 'running' %}
                                    <span class="px-2 py-1 rounded-full text-xs bg-green-500/20 text-green-400 flex items-center gap-1 w-fit">
                                        <i class="fas fa-circle text-[6px]"></i> Running
                                    </span>
                                    {% else %}
                                    <span class="px-2 py-1 rounded-full text-xs bg-red-500/20 text-red-400 flex items-center gap-1 w-fit">
                                        <i class="fas fa-circle text-[6px]"></i> Stopped
                                    </span>
                                    {% endif %}
                                </td>
                                
                                <!-- Expiry -->
                                <td class="p-4">
                                    <span class="text-yellow-400 text-xs">
                                        <i class="far fa-calendar-alt mr-1"></i>
                                        {{ server.expiry[:10] if server.expiry else 'N/A' }}
                                    </span>
                                </td>
                                
                                <!-- Actions -->
                                <td class="p-4">
                                    <div class="flex gap-2">
                                        <button onclick="changeCPULimit('{{ server.server_id }}', {{ server.cpu_limit | default(80) }})" 
                                            class="text-blue-400 hover:text-blue-300 text-sm bg-blue-500/10 px-2 py-1 rounded transition"
                                            title="Change CPU Limit">
                                            <i class="fas fa-tachometer-alt"></i>
                                        </button>
                                        <button onclick="deleteServer('{{ user.username }}', '{{ server.server_id }}')" 
                                            class="text-red-400 hover:text-red-300 text-sm bg-red-500/10 px-2 py-1 rounded transition"
                                            title="Delete Server">
                                            <i class="fas fa-trash"></i>
                                        </button>
                                    </div>
                                </td>
                            </tr>
                            {% endfor %}
                        {% endfor %}
                    </tbody>
                </table>
            </div>
        </div>
    </div>

    <script>
        // Quick CPU Preset
        function setQuickCPU(value) {
            document.getElementById('cpuLimit').value = value;
            document.getElementById('cpuLimitValue').innerText = value + '%';
        }

        // Quick Template
        function quickTemplate(type) {
            document.getElementById('serverType').value = 'python';
            
            if (type === 'discord') {
                document.getElementById('newUsername').value = 'discord_bot_' + Math.random().toString(36).substr(2, 4);
                document.getElementById('ram').value = '256MB';
                document.getElementById('cpuLimit').value = 50;
                document.getElementById('cpuLimitValue').innerText = '50%';
            } else if (type === 'telegram') {
                document.getElementById('newUsername').value = 'telegram_bot_' + Math.random().toString(36).substr(2, 4);
                document.getElementById('ram').value = '128MB';
                document.getElementById('cpuLimit').value = 30;
                document.getElementById('cpuLimitValue').innerText = '30%';
            } else if (type === 'flask') {
                document.getElementById('newUsername').value = 'flask_app_' + Math.random().toString(36).substr(2, 4);
                document.getElementById('ram').value = '512MB';
                document.getElementById('cpuLimit').value = 80;
                document.getElementById('cpuLimitValue').innerText = '80%';
            } else if (type === 'node') {
                document.getElementById('serverType').value = 'node';
                document.getElementById('newUsername').value = 'node_app_' + Math.random().toString(36).substr(2, 4);
                document.getElementById('ram').value = '256MB';
                document.getElementById('cpuLimit').value = 60;
                document.getElementById('cpuLimitValue').innerText = '60%';
            }
            
            document.getElementById('newPassword').value = generatePassword();
        }

        // Generate Random Password
        function generatePassword() {
            const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
            let password = '';
            for (let i = 0; i < 12; i++) {
                password += chars.charAt(Math.floor(Math.random() * chars.length));
            }
            return password;
        }

        // Toggle Password
        function togglePassword(serverId, password) {
            const passElement = document.getElementById('pass-' + serverId);
            if (passElement.innerText === '••••••••') {
                passElement.innerText = password;
            } else {
                passElement.innerText = '••••••••';
            }
        }

        // Change CPU Limit
        function changeCPULimit(serverId, currentLimit) {
            Swal.fire({
                title: 'Change CPU Rate Limit',
                text: `Current limit: ${currentLimit}%`,
                input: 'range',
                inputAttributes: {
                    min: '10',
                    max: '100',
                    step: '5'
                },
                inputValue: currentLimit,
                background: '#12141c',
                color: '#fff',
                confirmButtonColor: '#3b82f6',
                showCancelButton: true,
                inputValue: currentLimit,
                didOpen: () => {
                    const range = Swal.getInput();
                    const valueDisplay = document.createElement('span');
                    valueDisplay.style.cssText = 'display:block;text-align:center;font-size:2em;font-weight:bold;color:#3b82f6;margin-top:10px;';
                    valueDisplay.textContent = range.value + '%';
                    range.parentNode.insertBefore(valueDisplay, range.nextSibling);
                    
                    range.addEventListener('input', () => {
                        valueDisplay.textContent = range.value + '%';
                    });
                }
            }).then((result) => {
                if (result.isConfirmed) {
                    const newLimit = result.value;
                    fetch(`/admin/set_rate_limit/${serverId}`, {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({cpu_limit: parseInt(newLimit)})
                    })
                    .then(r => r.json())
                    .then(data => {
                        if (data.success) {
                            Swal.fire({
                                title: 'Updated!',
                                html: `CPU Limit set to <strong>${newLimit}%</strong>`,
                                icon: 'success',
                                background: '#12141c',
                                color: '#fff'
                            });
                            setTimeout(() => location.reload(), 1000);
                        } else {
                            Swal.fire('Error', data.error, 'error');
                        }
                    });
                }
            });
        }

        // Create Server
        function createServer() {
            const data = {
                username: document.getElementById('newUsername').value.trim(),
                password: document.getElementById('newPassword').value.trim(),
                server_type: document.getElementById('serverType').value,
                ram: document.getElementById('ram').value,
                disk: document.getElementById('disk').value,
                expiry_days: parseInt(document.getElementById('expiryDays').value) || 30,
                cpu_limit: parseInt(document.getElementById('cpuLimit').value) || 80
            };
            
            if (!data.username || !data.password) {
                Swal.fire({
                    title: 'Error',
                    text: 'Username and password are required!',
                    icon: 'error',
                    background: '#12141c',
                    color: '#fff'
                });
                return;
            }
            
            if (data.cpu_limit < 10 || data.cpu_limit > 100) {
                Swal.fire({
                    title: 'Error',
                    text: 'CPU limit must be between 10% and 100%!',
                    icon: 'error',
                    background: '#12141c',
                    color: '#fff'
                });
                return;
            }
            
            Swal.fire({
                title: 'Creating Server...',
                html: 'Please wait...',
                allowOutsideClick: false,
                didOpen: () => {
                    Swal.showLoading();
                },
                background: '#12141c',
                color: '#fff'
            });
            
            fetch('/admin/create_server', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(data)
            })
            .then(res => res.json())
            .then(data => {
                if (data.success) {
                    Swal.fire({
                        title: '🎉 Server Created!',
                        html: `
                            <div class="text-left space-y-2">
                                <p><strong>Username:</strong> ${data.username}</p>
                                <p><strong>Password:</strong> ${data.password}</p>
                                <p><strong>CPU Limit:</strong> ${data.cpu_limit || '80'}%</p>
                                <p><strong>Login URL:</strong> <a href="${data.login_url}" target="_blank" class="text-blue-400">${data.login_url}</a></p>
                                <p><strong>Host:</strong> ${data.hostname}</p>
                            </div>
                        `,
                        icon: 'success',
                        background: '#12141c',
                        color: '#fff',
                        confirmButtonColor: '#3b82f6'
                    });
                    
                    // Clear form
                    document.getElementById('newUsername').value = '';
                    document.getElementById('newPassword').value = '';
                    document.getElementById('cpuLimit').value = 80;
                    document.getElementById('cpuLimitValue').innerText = '80%';
                    
                    setTimeout(() => location.reload(), 3000);
                } else {
                    Swal.fire('Error', data.error || 'Something went wrong', 'error');
                }
            })
            .catch(err => {
                Swal.fire('Error', 'Could not connect to server', 'error');
            });
        }
        
        // Delete Server
        function deleteServer(username, serverId) {
            Swal.fire({
                title: 'Delete Server?',
                html: `
                    <p>Are you sure you want to delete this server?</p>
                    <p class="text-red-400 text-sm mt-2"><strong>User:</strong> ${username}</p>
                    <p class="text-red-400 text-sm"><strong>Server ID:</strong> ${serverId}</p>
                    <p class="text-yellow-400 text-xs mt-3">⚠️ This will delete all files and cannot be undone!</p>
                `,
                icon: 'warning',
                background: '#12141c',
                color: '#fff',
                showCancelButton: true,
                confirmButtonColor: '#ef4444',
                cancelButtonColor: '#374151',
                confirmButtonText: 'Yes, delete it!'
            }).then((result) => {
                if (result.isConfirmed) {
                    fetch(`/admin/delete_server/${username}/${serverId}`, {method: 'POST'})
                        .then(() => {
                            Swal.fire({
                                title: 'Deleted!',
                                text: 'Server has been deleted.',
                                icon: 'success',
                                background: '#12141c',
                                color: '#fff'
                            });
                            setTimeout(() => location.reload(), 1000);
                        });
                }
            });
        }

        // Auto refresh stats
        function refreshStats() {
            location.reload();
        }
        
        // Shortcut keys
        document.addEventListener('keydown', function(e) {
            if (e.ctrlKey && e.key === 'r') {
                e.preventDefault();
                location.reload();
            }
            if (e.ctrlKey && e.key === 'n') {
                e.preventDefault();
                document.getElementById('newUsername').focus();
            }
        });
    </script>
</body>
</html>''',

'error.html': r'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Server Error | SADNAN HOSTING</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css">
</head>
<body class="bg-[#0a0c12] min-h-screen flex items-center justify-center p-4">
    <div class="bg-[#12141c] rounded-2xl p-8 w-full max-w-md border text-center">
        
        {% if error_type == "expired" %}
        <div class="w-20 h-20 bg-yellow-500/20 rounded-2xl flex items-center justify-center mx-auto mb-4 border border-yellow-500">
            <i class="fas fa-hourglass-end text-4xl text-yellow-500"></i>
        </div>
        <h1 class="text-2xl font-bold text-yellow-500 mb-2">⚠️ Server Expired</h1>
        <p class="text-gray-400 mb-4">Your server subscription has ended.</p>
        <p class="text-gray-500 text-sm">Contact admin to renew.</p>
        
        {% elif error_type == "deleted" %}
        <div class="w-20 h-20 bg-red-500/20 rounded-2xl flex items-center justify-center mx-auto mb-4 border border-red-500">
            <i class="fas fa-trash-alt text-4xl text-red-500"></i>
        </div>
        <h1 class="text-2xl font-bold text-red-500 mb-2">❌ Server Not Found</h1>
        <p class="text-gray-400 mb-4">This server has been deleted.</p>
        <p class="text-gray-500 text-sm">Contact admin for details.</p>
        
        {% else %}
        <div class="w-20 h-20 bg-gray-500/20 rounded-2xl flex items-center justify-center mx-auto mb-4 border border-gray-500">
            <i class="fas fa-question text-4xl text-gray-500"></i>
        </div>
        <h1 class="text-2xl font-bold text-gray-400 mb-2">Error</h1>
        <p class="text-gray-500">Something went wrong.</p>
        {% endif %}
        
        <a href="https://t.me/YOUR_TELEGRAM" target="_blank" 
           class="inline-flex items-center gap-2 bg-gradient-to-r from-blue-600 to-blue-500 hover:from-blue-500 hover:to-blue-600 text-white font-bold py-2.5 px-6 rounded-lg transition transform hover:scale-105 mt-6">
            <i class="fab fa-telegram-plane"></i> Contact Admin
        </a>
    </div>
</body>
</html>''',

'home.html': r'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>SADNAN HOSTING | Panel</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css">
    <style>
        body { background-color: #0c0e14; color: #fff; font-family: 'Segoe UI', sans-serif; }
        .console-box { background:#0a0a0a; font-family:'Courier New',monospace; height:420px; overflow-y:auto; padding:15px; color:#e0e0e0; border:1px solid #2a2a2a; border-radius:10px; font-size:13px; white-space:pre-wrap; line-height:1.5; }
        .console-box::-webkit-scrollbar { width:6px; }
        .console-box::-webkit-scrollbar-track { background:#1a1a1a; }
        .console-box::-webkit-scrollbar-thumb { background:#3b82f6; border-radius:3px; }
        .tab-active { color:#3b82f6; border-bottom:2px solid #3b82f6; font-weight:600; }
        
        .toast-container { position:fixed; bottom:20px; right:20px; z-index:9999; display:flex; flex-direction:column; gap:10px; }
        .toast { background:#1a1d27; border:1px solid #2a2d37; border-radius:12px; padding:14px 18px; min-width:300px; max-width:380px; display:flex; align-items:center; gap:12px; animation:slideIn 0.3s ease-out; box-shadow:0 10px 40px rgba(0,0,0,0.5); }
        .toast.success { border-left:4px solid #10b981; }
        .toast.error { border-left:4px solid #ef4444; }
        .toast.info { border-left:4px solid #3b82f6; }
        .toast.warning { border-left:4px solid #f59e0b; }
        @keyframes slideIn { from{transform:translateX(400px);opacity:0} to{transform:translateX(0);opacity:1} }
        @keyframes slideOut { from{transform:translateX(0);opacity:1} to{transform:translateX(400px);opacity:0} }
        
        .spinner { width:20px;height:20px;border:3px solid #2a2d37;border-top:3px solid #3b82f6;border-radius:50%;animation:spin 0.8s linear infinite;flex-shrink:0; }
        @keyframes spin { to{transform:rotate(360deg)} }
        
        .stat-card { background:#12141c; border:1px solid #1e2130; border-radius:12px; padding:14px; transition:all 0.3s; }
        .stat-card:hover { border-color:#3b82f6; }
        .btn { transition:all 0.2s; font-weight:600; font-size:13px; }
        .btn:active { transform:scale(0.95); }
        
        .status-dot { display:inline-block;width:6px;height:6px;border-radius:50%;margin-right:4px;vertical-align:middle;position:relative;top:-1px; }
        
        .dropdown { position:relative; display:inline-block; }
        .dropdown-menu {
            position:absolute; right:0; top:100%; background:#1a1d27; border:1px solid #2a2d37;
            border-radius:8px; min-width:140px; z-index:100; display:none; box-shadow:0 10px 30px rgba(0,0,0,0.5);
        }
        .dropdown-menu.show { display:block; }
        .dropdown-menu button {
            display:block; width:100%; text-align:left; padding:8px 14px; font-size:12px;
            color:#d1d5db; transition:all 0.2s; border:none; background:none; cursor:pointer;
        }
        .dropdown-menu button:hover { background:#252836; color:#fff; }
        .dropdown-menu button:first-child { border-radius:8px 8px 0 0; }
        .dropdown-menu button:last-child { border-radius:0 0 8px 8px; }
        .dropdown-menu button i { width:16px; margin-right:8px; }
        
        .folder-link { cursor:pointer; color:#eab308; }
        .folder-link:hover { text-decoration:underline; }

        .github-card { background:#12141c; border:1px solid #2a2d37; border-radius:12px; padding:20px; }
        .github-card:hover { border-color:#3b82f6; }
        .mini-terminal { 
            background:#0b0b0e; font-family:'Courier New',monospace; 
            height:180px; overflow-y:auto; padding:12px; color:#a0a0a0; 
            border:1px solid #222; border-radius:8px; font-size:11px; 
            white-space:pre-wrap; line-height:1.4; 
        }
        .mini-terminal::-webkit-scrollbar { width:4px; }
        .mini-terminal::-webkit-scrollbar-track { background:#111; }
        .mini-terminal::-webkit-scrollbar-thumb { background:#f59e0b; border-radius:2px; }

        .profile-card {
            background:#12141c;
            border:1px solid #2a2d37;
            border-radius:16px;
            padding:24px;
            width:380px;
            margin:0 auto;
        }
    </style>
</head>
<body>

    <div class="toast-container" id="toastContainer"></div>

    <header class="h-16 bg-[#12141c] border-b border-gray-800 flex items-center px-6 justify-between sticky top-0 z-10">
        <div class="flex items-center gap-3">
            <div class="w-10 h-10 flex items-center justify-center rounded-xl bg-gradient-to-br from-blue-600 to-blue-800">
                <i class="fas fa-cloud-upload-alt text-white"></i>
            </div>
            <div>
                <h1 class="text-lg font-black">SADNAN <span class="text-blue-500">HOSTING</span></h1>
                <p class="text-[10px] text-gray-400">{{ username }}</p>
            </div>
        </div>
        <a href="/logout" class="text-red-500 hover:text-red-400"><i class="fas fa-sign-out-alt"></i> Logout</a>
    </header>

    <main class="container mx-auto p-4 max-w-7xl">
        {% if current_server %}
        
        <div class="bg-[#12141c] rounded-xl border border-gray-800 p-3 mb-4 flex justify-between items-center flex-wrap gap-3">
            <div class="flex flex-wrap gap-3 text-xs">
                <span class="text-gray-400">ID: <span class="text-blue-400 font-mono">{{ current_server.server_id[:8] }}</span></span>
                <span class="text-gray-400">CPU Limit: <span class="text-yellow-400">{{ current_server.cpu_limit | default(80) }}%</span></span>
                <span class="text-gray-400">Expires: <span class="text-yellow-500">{{ current_server.expiry[:10] if current_server.expiry else 'N/A' }}</span></span>
            </div>
            <div class="flex gap-2">
                <button onclick="runServer()" class="bg-green-600 hover:bg-green-500 px-3 py-1.5 rounded-lg text-xs btn"><i class="fas fa-play"></i> START</button>
                <button onclick="stopServer()" class="bg-red-600 hover:bg-red-500 px-3 py-1.5 rounded-lg text-xs btn"><i class="fas fa-stop"></i> STOP</button>
                <button onclick="restartServer()" class="bg-yellow-600 hover:bg-yellow-500 px-3 py-1.5 rounded-lg text-xs btn"><i class="fas fa-sync-alt"></i> RESTART</button>
                <button onclick="clearLogs()" class="bg-gray-600 hover:bg-gray-500 px-3 py-1.5 rounded-lg text-xs btn"><i class="fas fa-eraser"></i> CLEAR</button>
            </div>
        </div>

        <div class="flex gap-6 border-b border-gray-800 mb-4">
            <button onclick="showTab('console')" id="tab-console" class="pb-2 px-1 text-sm tab-active"><i class="fas fa-terminal mr-1"></i>Console</button>
            <button onclick="showTab('files')" id="tab-files" class="pb-2 px-1 text-sm text-gray-400"><i class="fas fa-folder mr-1"></i>Files</button>
            <button onclick="showTab('github')" id="tab-github" class="pb-2 px-1 text-sm text-gray-400"><i class="fab fa-github mr-1"></i>GitHub</button>
            <button onclick="showTab('startup')" id="tab-startup" class="pb-2 px-1 text-sm text-gray-400"><i class="fas fa-rocket mr-1"></i>Startup</button>
            <button onclick="showTab('scripts')" id="tab-scripts" class="pb-2 px-1 text-sm text-gray-400"><i class="fas fa-play-circle mr-1"></i>Scripts</button>
            <button onclick="showTab('profile')" id="tab-profile" class="pb-2 px-1 text-sm text-gray-400"><i class="fas fa-user-circle mr-1"></i>My Profile</button>
        </div>

        <!-- ============ CONSOLE TAB ============ -->
        <div id="console-tab">
            <div class="console-box" id="logs">[INFO] Ready. Click START...</div>
            <div class="flex gap-2 mt-3">
                <div class="relative flex-1">
                    <span class="absolute inset-y-0 left-3 flex items-center text-gray-500">$</span>
                    <input type="text" id="cmdInput" onkeydown="if(event.key==='Enter')sendCmd()" placeholder="Type command..." 
                        class="w-full bg-[#12141c] border border-gray-700 rounded-lg p-3 pl-8 text-sm outline-none focus:border-blue-500">
                </div>
                <button onclick="sendCmd()" class="bg-blue-600 hover:bg-blue-500 px-5 rounded-lg text-sm btn">
                    <i class="fas fa-paper-plane mr-1"></i>Send
                </button>
            </div>
            
            <div class="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3 mt-4">
                <div class="stat-card">
                    <p class="text-[10px] text-gray-500 mb-1"><i class="fas fa-link mr-1"></i>ADDRESS</p>
                    <p class="text-xs text-blue-400 font-mono truncate" id="serverAddr" title="Server Address"></p>
                </div>
                <div class="stat-card">
                    <p class="text-[10px] text-gray-500 mb-1"><i class="fas fa-microchip mr-1"></i>CPU</p>
                    <p class="text-sm font-bold text-green-400" id="cpuDisplay">0%</p>
                </div>
                <div class="stat-card">
                    <p class="text-[10px] text-gray-500 mb-1"><i class="fas fa-memory mr-1"></i>RAM</p>
                    <p class="text-sm font-bold text-purple-400" id="ramDisplay">0 MB</p>
                </div>
                <div class="stat-card">
                    <p class="text-[10px] text-gray-500 mb-1"><i class="fas fa-download mr-1"></i>NET IN</p>
                    <p class="text-sm font-bold text-cyan-400" id="netInDisplay">0 KB</p>
                </div>
                <div class="stat-card">
                    <p class="text-[10px] text-gray-500 mb-1"><i class="fas fa-upload mr-1"></i>NET OUT</p>
                    <p class="text-sm font-bold text-orange-400" id="netOutDisplay">0 KB</p>
                </div>
                <div class="stat-card">
                    <p class="text-[10px] text-gray-500 mb-1">STATUS</p>
                    <p class="text-sm font-bold" id="statusDisplay">
                        <span class="text-red-400"><span class="status-dot" style="background:#ef4444;"></span>STOPPED</span>
                    </p>
                </div>
            </div>
        </div>

        <!-- ============ FILES TAB ============ -->
        <div id="files-tab" class="hidden">
            <div class="mb-4 flex justify-between flex-wrap gap-2 items-center">
                <span class="text-sm text-gray-300" id="currentPathDisplay">/</span>
                <div class="flex gap-2">
                    <input type="file" id="fileInput" class="hidden" onchange="uploadFile()">
                    <button onclick="document.getElementById('fileInput').click()" class="bg-blue-600 hover:bg-blue-500 px-3 py-1.5 rounded-lg text-xs"><i class="fas fa-upload"></i> Upload (Max 50MB)</button>
                    <button onclick="createFolder()" class="bg-gray-600 hover:bg-gray-500 px-3 py-1.5 rounded-lg text-xs"><i class="fas fa-folder-plus"></i> Folder</button>
                    <button onclick="createFile()" class="bg-gray-600 hover:bg-gray-500 px-3 py-1.5 rounded-lg text-xs"><i class="fas fa-file-plus"></i> File</button>
                </div>
            </div>
            <div class="bg-[#12141c] rounded-xl border border-gray-800">
                <div id="fileList" class="divide-y divide-gray-800">
                    <div class="p-6 text-center text-gray-500">Loading...</div>
                </div>
            </div>
        </div>

        <!-- ============ GITHUB TAB ============ -->
        <div id="github-tab" class="hidden">
            <div class="grid md:grid-cols-2 gap-4 mb-4">
                <!-- Public Repository -->
                <div class="github-card">
                    <div class="flex items-center gap-2 mb-4">
                        <div class="w-10 h-10 bg-green-500/20 rounded-lg flex items-center justify-center">
                            <i class="fas fa-globe text-green-400 text-lg"></i>
                        </div>
                        <div>
                            <h3 class="font-bold text-sm">Public Repository</h3>
                            <p class="text-gray-500 text-[10px]">No token required</p>
                        </div>
                    </div>
                    <label class="text-gray-400 text-[11px] mb-1.5 block">Repo URL</label>
                    <input type="text" id="publicRepoUrl" placeholder="https://github.com/user/repo" 
                        class="w-full bg-[#0c0e14] border border-gray-700 rounded-lg px-4 py-2.5 text-sm text-white focus:border-blue-500 outline-none mb-4">
                    <button onclick="deployRepo(false)" 
                        class="w-full bg-green-600 hover:bg-green-500 py-2.5 rounded-lg text-sm font-bold transition">
                        <i class="fas fa-download mr-2"></i>Deploy Public Repo
                    </button>
                </div>

                <!-- Private Repository -->
                <div class="github-card">
                    <div class="flex items-center gap-2 mb-4">
                        <div class="w-10 h-10 bg-yellow-500/20 rounded-lg flex items-center justify-center">
                            <i class="fas fa-lock text-yellow-400 text-lg"></i>
                        </div>
                        <div>
                            <h3 class="font-bold text-sm">Private Repository</h3>
                            <p class="text-gray-500 text-[10px]">Access token required</p>
                        </div>
                    </div>
                    <label class="text-gray-400 text-[11px] mb-1.5 block">Repo URL</label>
                    <input type="text" id="privateRepoUrl" placeholder="https://github.com/user/repo" 
                        class="w-full bg-[#0c0e14] border border-gray-700 rounded-lg px-4 py-2.5 text-sm text-white focus:border-blue-500 outline-none mb-3">
                    <label class="text-gray-400 text-[11px] mb-1.5 block">Access Token</label>
                    <input type="password" id="accessToken" placeholder="ghp_xxxxxxxxxxxx" 
                        class="w-full bg-[#0c0e14] border border-gray-700 rounded-lg px-4 py-2.5 text-sm text-white focus:border-blue-500 outline-none mb-4">
                    <button onclick="deployRepo(true)" 
                        class="w-full bg-yellow-600 hover:bg-yellow-500 py-2.5 rounded-lg text-sm font-bold transition">
                        <i class="fas fa-download mr-2"></i>Deploy Private Repo
                    </button>
                </div>
            </div>

            <!-- Mini Terminal (NO Send button) -->
            <div class="bg-[#12141c] rounded-xl border border-gray-800 p-4">
                <div class="flex justify-between items-center mb-2">
                    <h4 class="text-xs text-gray-400 flex items-center gap-2">
                        <i class="fas fa-terminal text-yellow-500"></i> Deployment Terminal
                    </h4>
                    <button onclick="clearGitLogs()" 
                        class="bg-gray-700 hover:bg-gray-600 px-3 py-1 rounded text-[11px] transition">
                        <i class="fas fa-eraser mr-1"></i>Clear
                    </button>
                </div>
                <div class="mini-terminal" id="gitLogs">> Ready for deployment...</div>
            </div>
        </div>

        <!-- ============ STARTUP TAB ============ -->
        <div id="startup-tab" class="hidden">
            <div class="bg-[#12141c] rounded-xl border border-gray-800 p-5">
                <h3 class="text-md font-bold mb-4 text-blue-400"><i class="fas fa-cog mr-2"></i>Startup Config</h3>
                <div class="mb-4">
                    <label class="block text-gray-400 text-sm mb-1">Main File *</label>
                    <input type="text" id="mainFileInput" value="{{ current_server.main_file | default('main.py') }}" class="w-full bg-[#0c0e14] border border-gray-700 rounded-lg px-3 py-2 text-sm text-white">
                </div>
                <div class="mb-4">
                    <label class="block text-gray-400 text-sm mb-1">Requirements File</label>
                    <input type="text" id="requirementsInput" value="{{ current_server.requirements_file | default('requirements.txt') }}" class="w-full bg-[#0c0e14] border border-gray-700 rounded-lg px-3 py-2 text-sm text-white">
                </div>
                <button onclick="saveStartup()" class="bg-blue-600 hover:bg-blue-500 px-5 py-2 rounded-lg text-sm font-bold"><i class="fas fa-save mr-1"></i> Save</button>
            </div>
        </div>

        <!-- ============ SCRIPTS TAB (multi-file runner) ============ -->
        <div id="scripts-tab" class="hidden">
            <div class="bg-[#12141c] rounded-xl border border-gray-800 p-5">
                <div class="flex items-center justify-between mb-1">
                    <h3 class="text-md font-bold text-blue-400"><i class="fas fa-play-circle mr-2"></i>Run Any File</h3>
                    <span id="scriptCount" class="text-gray-400 text-xs"></span>
                </div>
                <div class="flex gap-2 mb-3">
                    <button onclick="scriptAll('run')" class="bg-green-600 hover:bg-green-500 px-3 py-1 rounded-lg text-xs font-bold">Start All</button>
                    <button onclick="scriptAll('stop')" class="bg-red-600 hover:bg-red-500 px-3 py-1 rounded-lg text-xs font-bold">Stop All</button>
                </div>
                <p class="text-gray-500 text-xs mb-4">Protita .py file alada alada chalao. Sob ek shathe cholte pare, main.py lagbe na. Browser bondho korleo cholte thakbe.</p>
                <div id="scriptList" class="space-y-2"></div>
                <div class="mt-4">
                    <div class="flex items-center justify-between mb-1">
                        <span class="text-gray-400 text-xs" id="scriptLogTitle">Logs: file select koro</span>
                    </div>
                    <pre id="scriptLogs" class="bg-[#0c0e14] border border-gray-800 rounded-lg p-3 text-xs text-green-300 overflow-auto" style="max-height:260px;min-height:80px;white-space:pre-wrap;"></pre>
                </div>
            </div>
        </div>

        <!-- ============ MY PROFILE TAB (Square Card) ============ -->
        <div id="profile-tab" class="hidden">
            <div class="flex justify-center pt-4">
                <div class="profile-card">
                    <h3 class="text-md font-bold mb-1 text-blue-400"><i class="fas fa-user-circle mr-2"></i>Account Settings</h3>
                    <p class="text-gray-500 text-xs mb-5">Update your hosting credentials</p>
                    
                    <div class="mb-3">
                        <label class="block text-gray-400 text-xs mb-1.5">New Username</label>
                        <input type="text" id="newUsername" value="{{ username }}" 
                            class="w-full bg-[#0c0e14] border border-gray-700 rounded-lg px-4 py-2.5 text-sm text-white focus:border-blue-500 outline-none">
                    </div>
                    <div class="mb-3">
                        <label class="block text-gray-400 text-xs mb-1.5">New Password</label>
                        <input type="password" id="newPassword" placeholder="Enter new password" 
                            class="w-full bg-[#0c0e14] border border-gray-700 rounded-lg px-4 py-2.5 text-sm text-white focus:border-blue-500 outline-none">
                    </div>
                    <div class="mb-5">
                        <label class="block text-gray-400 text-xs mb-1.5">Confirm New Password</label>
                        <input type="password" id="confirmPassword" placeholder="Confirm new password" 
                            class="w-full bg-[#0c0e14] border border-gray-700 rounded-lg px-4 py-2.5 text-sm text-white focus:border-blue-500 outline-none">
                    </div>
                    <button onclick="updateCredentials()" 
                        class="w-full bg-blue-600 hover:bg-blue-500 py-2.5 rounded-lg text-sm font-bold transition mb-5">
                        <i class="fas fa-check mr-1"></i> Update Credentials
                    </button>

                    <!-- System Info -->
                    <div class="border-t border-gray-800 pt-4 mb-4">
                        <h4 class="text-xs text-gray-400 mb-2">System Info</h4>
                        <div class="space-y-2 text-xs">
                            <div class="text-green-400"><span class="status-dot" style="background:#10b981;"></span> Node Status: Active</div>
                            <div class="text-blue-400"><i class="fas fa-shield-alt mr-1"></i> Security: Encrypted</div>
                            <div class="text-gray-400"><i class="fas fa-code-branch mr-1"></i> Version: v3.1 Stable</div>
                        </div>
                    </div>

                    <!-- Social Support -->
                    <div class="border-t border-gray-800 pt-4">
                        <h4 class="text-xs text-gray-400 mb-2">Social Support</h4>
                        <a href="https://t.me/YOUR_TELEGRAM" target="_blank" 
                            class="inline-flex items-center gap-2 bg-blue-600/20 hover:bg-blue-600/30 border border-blue-500/30 px-4 py-2 rounded-lg text-sm transition">
                            <i class="fab fa-telegram-plane text-blue-400"></i>
                            <span class="text-blue-300">Telegram Support</span>
                        </a>
                    </div>
                </div>
            </div>
        </div>
        
        {% else %}
        <div class="text-center text-gray-500 py-20"><i class="fas fa-server text-6xl mb-4 opacity-30"></i><p>No server found.</p></div>
        {% endif %}
    </main>

    <div id="editModal" class="hidden fixed inset-0 bg-black bg-opacity-80 flex items-center justify-center p-4 z-50">
        <div class="bg-[#12141c] w-full max-w-3xl rounded-xl border border-blue-500">
            <div class="p-3 border-b border-gray-700 flex justify-between"><h3 id="editTitle" class="text-blue-400 text-sm"></h3><button onclick="closeEdit()" class="text-red-500 text-xl">&times;</button></div>
            <textarea id="editor" class="w-full h-96 bg-black text-green-400 p-3 font-mono text-sm outline-none resize-none"></textarea>
            <div class="p-3 border-t border-gray-700 text-right"><button onclick="saveFileContent()" class="bg-blue-600 px-4 py-1.5 rounded-lg text-sm"><i class="fas fa-check"></i> Save</button></div>
        </div>
    </div>

    <script>
        let currentServerId = '{{ current_server.server_id }}';
        let editingFile = null;
        let activeTab = 'console';
        let activeToast = null;
        let currentFolder = '';
        const MAX_FILE_SIZE = 50 * 1024 * 1024;

        document.getElementById('serverAddr').innerText = window.location.href;

        document.addEventListener('click', function(e) {
            if (!e.target.closest('.dropdown')) {
                document.querySelectorAll('.dropdown-menu').forEach(m => m.classList.remove('show'));
            }
        });

        function showToast(message, type='info', duration=4000) {
            const container = document.getElementById('toastContainer');
            if (activeToast && type !== 'info') { updateToast(activeToast, message, type, duration); return; }
            const toast = document.createElement('div'); toast.className = `toast ${type}`;
            let iconHTML = '';
            if (type === 'info') iconHTML = '<div class="spinner"></div>';
            else if (type === 'success') iconHTML = '<i class="fas fa-check-circle text-green-400 text-lg"></i>';
            else if (type === 'error') iconHTML = '<i class="fas fa-times-circle text-red-400 text-lg"></i>';
            else if (type === 'warning') iconHTML = '<i class="fas fa-exclamation-triangle text-yellow-400 text-lg"></i>';
            toast.innerHTML = `<div class="flex-shrink-0">${iconHTML}</div><div class="flex-1"><p class="text-sm">${message}</p></div><button onclick="this.parentElement.remove();activeToast=null;" class="text-gray-500 hover:text-gray-300"><i class="fas fa-times text-xs"></i></button>`;
            container.appendChild(toast);
            if (type === 'info') activeToast = toast;
            if (duration > 0 && type !== 'info') {
                setTimeout(() => { toast.style.animation = 'slideOut 0.3s ease-out forwards'; setTimeout(() => { toast.remove(); if(activeToast===toast) activeToast=null; }, 300); }, duration);
            }
        }

        function updateToast(toast, message, type, duration) {
            const iconDiv = toast.querySelector('.flex-shrink-0'); const msgP = toast.querySelector('.text-sm');
            if (iconDiv) {
                if (type === 'success') iconDiv.innerHTML = '<i class="fas fa-check-circle text-green-400 text-lg"></i>';
                else if (type === 'error') iconDiv.innerHTML = '<i class="fas fa-times-circle text-red-400 text-lg"></i>';
                else if (type === 'warning') iconDiv.innerHTML = '<i class="fas fa-exclamation-triangle text-yellow-400 text-lg"></i>';
            }
            if (msgP) msgP.textContent = message;
            toast.className = `toast ${type}`; activeToast = null;
            if (duration > 0) { setTimeout(() => { toast.style.animation = 'slideOut 0.3s ease-out forwards'; setTimeout(() => toast.remove(), 300); }, duration); }
        }

        function toggleDropdown(event, fileId) {
            event.stopPropagation();
            document.querySelectorAll('.dropdown-menu').forEach(m => { if (m.id !== 'dropdown-' + fileId) m.classList.remove('show'); });
            document.getElementById('dropdown-' + fileId).classList.toggle('show');
        }


        // ===== Multi-file runner =====
        let selectedScript = null;
        function fmtUptime(iso) {
            if(!iso) return '';
            const t = Math.max(0, Math.floor((Date.now() - new Date(iso.replace(' ','T'))) / 1000));
            if(t < 60) return t + 's';
            if(t < 3600) return Math.floor(t/60) + 'm';
            return Math.floor(t/3600) + 'h ' + Math.floor((t%3600)/60) + 'm';
        }
        function loadScripts() {
            fetch(`/api/scripts/${currentServerId}`).then(r=>r.json()).then(data => {
                const box = document.getElementById('scriptList');
                if(!box) return;
                document.getElementById('scriptCount').textContent = `${data.running||0} running / ${data.scripts.length} files`;
                box.innerHTML = '';
                if(!data.scripts.length) { box.innerHTML = '<p class="text-gray-500 text-sm">Kono .py file nai. Files tab theke upload koro.</p>'; return; }
                data.scripts.forEach(sc => {
                    const row = document.createElement('div');
                    row.className = 'flex items-center bg-[#0c0e14] border rounded-lg px-3 py-2 ' + (sc.running ? 'border-green-700' : 'border-gray-800');
                    const num = document.createElement('span');
                    num.className = 'text-gray-500 text-xs font-bold w-6 shrink-0';
                    num.textContent = sc.serial + '.';
                    const name = document.createElement('button');
                    name.className = 'text-sm text-left text-white truncate flex-1';
                    name.innerHTML = `<span class="status-dot" style="background:${sc.running?'#22c55e':'#ef4444'};"></span> `;
                    name.appendChild(document.createTextNode(sc.file + (sc.main ? '  (main)' : '')));
                    if(sc.running) { const up = document.createElement('span'); up.className='text-green-400 text-xs ml-2'; up.textContent = fmtUptime(sc.started_at); name.appendChild(up); }
                    name.onclick = () => { selectedScript = sc.file; loadScriptLogs(); };
                    const act = document.createElement('button');
                    act.className = (sc.running ? 'bg-red-600 hover:bg-red-500' : 'bg-green-600 hover:bg-green-500') + ' px-3 py-1 rounded-lg text-xs font-bold ml-3';
                    act.textContent = sc.running ? 'Stop' : 'Start';
                    act.onclick = () => scriptAction(sc.running ? 'stop' : 'run', sc.file);
                    row.appendChild(num); row.appendChild(name); row.appendChild(act); box.appendChild(row);
                });
                window._scriptData = data.scripts;
            });
        }
        function scriptAction(action, file, quiet) {
            return fetch(`/api/script/${action}/${currentServerId}`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({file})})
                .then(r=>r.json()).then(d => {
                    if(!quiet) showToast(d.msg, d.status==='success'?'success':'error', 2500);
                    selectedScript = file;
                    loadScripts();            // sathe sathe list update
                    setTimeout(loadScriptLogs, 700);
                    return d;
                });
        }
        async function scriptAll(action) {
            const want = action === 'run';
            for (const sc of (window._scriptData || [])) {
                if (sc.running !== want) await scriptAction(action, sc.file, true);
            }
            showToast(want ? 'Sob file start hoyeche' : 'Sob file stop hoyeche', 'success', 2500);
        }
        function loadScriptLogs() {
            if(!selectedScript) return;
            document.getElementById('scriptLogTitle').textContent = 'Logs: ' + selectedScript;
            fetch(`/api/script/logs/${currentServerId}?file=${encodeURIComponent(selectedScript)}`).then(r=>r.json()).then(d => {
                const el = document.getElementById('scriptLogs');
                el.textContent = d.logs || 'No logs yet...';
                el.scrollTop = el.scrollHeight;
            });
        }
        setInterval(() => { if(activeTab==='scripts') { loadScripts(); loadScriptLogs(); } }, 3000);

        function showTab(tab) {
            activeTab = tab;
            ['console','files','github','startup','scripts','profile'].forEach(t => {
                const el = document.getElementById(`${t}-tab`);
                const btn = document.getElementById(`tab-${t}`);
                if(el) el.classList.toggle('hidden', t!==tab);
                if(btn) btn.className = t===tab ? 'pb-2 px-1 text-sm tab-active' : 'pb-2 px-1 text-sm text-gray-400';
            });
            if(tab==='files') loadFileList(currentFolder);
            if(tab==='github') loadGitLogs();
            if(tab==='startup') loadStartup();
            if(tab==='scripts') loadScripts();
        }

        function runServer() {
            showToast('Starting server...', 'info', 0);
            fetch(`/api/run/${currentServerId}`, {method:'POST'}).then(r=>r.json()).then(data => {
                if(data.status==='success') { showToast('Server started!', 'success', 3000); setTimeout(()=>location.reload(),1500); }
                else showToast(data.msg, 'error', 4000);
            }).catch(() => showToast('Connection failed!', 'error', 4000));
        }

        function stopServer() {
            showToast('Stopping server...', 'info', 0);
            fetch(`/api/stop/${currentServerId}`, {method:'POST'}).then(r=>r.json()).then(data => {
                if(data.status==='success') { showToast('Server stopped!', 'warning', 3000); setTimeout(()=>location.reload(),1500); }
                else showToast(data.msg, 'error', 4000);
            }).catch(() => showToast('Connection failed!', 'error', 4000));
        }

        function restartServer() {
            showToast('Restarting...', 'info', 0);
            fetch(`/api/stop/${currentServerId}`, {method:'POST'}).then(() => {
                setTimeout(() => {
                    fetch(`/api/run/${currentServerId}`, {method:'POST'}).then(r=>r.json()).then(data => {
                        if(data.status==='success') { showToast('Restarted!', 'success', 3000); setTimeout(()=>location.reload(),1500); }
                        else showToast(data.msg, 'error', 4000);
                    });
                }, 2000);
            });
        }

        function clearLogs() {
            fetch(`/api/clear_logs/${currentServerId}`, {method:'POST'}).then(r=>r.json()).then(data => {
                if(data.status==='success') { document.getElementById('logs').innerHTML = '[INFO] Cleared.'; showToast('Logs cleared!', 'success', 2000); }
                else showToast(data.msg, 'error', 3000);
            }).catch(() => showToast('Failed!', 'error', 3000));
        }

        function sendCmd() {
            let inp = document.getElementById('cmdInput'); let val = inp.value.trim(); if(!val) return;
            let d = new Date().toLocaleTimeString('en-US',{hour12:false});
            document.getElementById('logs').innerHTML += `\n<span style="color:#fa0">[${d}] $ ${val}</span>`;
            fetch('/api/command', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({cmd:val, server_id:currentServerId})}).then(()=>{inp.value='';});
        }

        function updateStats() {
            fetch(`/api/stats/${currentServerId}`).then(r=>r.json()).then(data => {
                document.getElementById('cpuDisplay').innerText = data.cpu || '0%';
                document.getElementById('ramDisplay').innerText = data.ram || '0 MB';
                document.getElementById('netInDisplay').innerText = data.net_in || '0 KB';
                document.getElementById('netOutDisplay').innerText = data.net_out || '0 KB';
                const status = (data.status||'stopped').toUpperCase();
                const isRunning = status === 'RUNNING';
                const color = isRunning ? '#10b981' : '#ef4444';
                const textColor = isRunning ? 'text-green-400' : 'text-red-400';
                document.getElementById('statusDisplay').innerHTML = `<span class="${textColor}"><span class="status-dot" style="background:${color};"></span>${status}</span>`;
            });
        }

        function loadLogs() {
            fetch(`/api/logs/${currentServerId}`).then(r=>r.json()).then(data => {
                const d = document.getElementById('logs');
                if(d) { d.innerHTML = data.logs || '[INFO] No logs yet...'; d.scrollTop = d.scrollHeight; }
            });
        }

        function updateBreadcrumb() {
            const display = document.getElementById('currentPathDisplay');
            if (!currentFolder) {
                display.innerHTML = '<i class="fas fa-folder-open mr-1 text-yellow-500"></i> /';
            } else {
                const parts = currentFolder.split('/').filter(p => p);
                let html = '<i class="fas fa-folder-open mr-1 text-yellow-500"></i> ';
                html += '<a onclick="navigateTo(\'\')" class="cursor-pointer text-blue-400 hover:underline">/</a>';
                let path = '';
                parts.forEach((part, i) => {
                    path += '/' + part;
                    if (i === parts.length - 1) {
                        html += `<span class="text-gray-400">${part}</span>`;
                    } else {
                        html += `<a onclick="navigateTo('${path}')" class="cursor-pointer text-blue-400 hover:underline">/${part}</a>`;
                    }
                });
                display.innerHTML = html;
            }
        }

        function navigateTo(folder) {
            currentFolder = folder;
            loadFileList(folder);
        }

        function loadFileList(folder = '') {
            currentFolder = folder;
            updateBreadcrumb();
            
            let url = `/api/files/${currentServerId}`;
            if (folder) url += `?folder=${encodeURIComponent(folder)}`;
            
            fetch(url).then(r=>r.json()).then(data => {
                if(!data.files||data.files.length===0) { 
                    document.getElementById('fileList').innerHTML=`<div class="p-6 text-center text-gray-500"><i class="fas fa-folder-open mr-2"></i>Empty folder</div>`; 
                    return; 
                }
                let h = '<table class="w-full text-sm"><tbody>';
                
                if (folder) {
                    const parentFolder = folder.split('/').slice(0, -1).join('/');
                    h += `<tr class="border-b border-gray-800 hover:bg-[#1a1d27] cursor-pointer" onclick="navigateTo('${parentFolder}')">
                        <td class="p-3" colspan="4"><i class="fas fa-arrow-left text-gray-400 mr-2"></i><span class="text-gray-400">.. (Go Back)</span></td>
                    </tr>`;
                }
                
                data.files.forEach((f, index) => {
                    const fileId = 'file-' + index;
                    
                    if (f.is_dir) {
                        const folderPath = folder ? folder + '/' + f.name : f.name;
                        h += `<tr class="border-b border-gray-800 hover:bg-[#1a1d27] cursor-pointer" onclick="navigateTo('${folderPath}')">
                            <td class="p-3"><i class="fas fa-folder text-yellow-500 mr-2"></i><span class="text-yellow-400">${f.name}</span></td>
                            <td class="p-3 text-gray-400 text-xs">-</td>
                            <td class="p-3 text-gray-500 text-xs">${f.modified}</td>
                            <td class="p-3 text-right">
                                <div class="dropdown" onclick="event.stopPropagation()">
                                    <button onclick="toggleDropdown(event, '${fileId}')" class="text-gray-400 hover:text-white px-2 py-1 rounded"><i class="fas fa-ellipsis-v"></i></button>
                                    <div class="dropdown-menu" id="dropdown-${fileId}">
                                        <button onclick="renameFilePrompt('${f.name}');document.querySelectorAll('.dropdown-menu').forEach(m=>m.classList.remove('show'));"><i class="fas fa-i-cursor text-yellow-400"></i> Rename</button>
                                        <button onclick="deleteFile('${f.name}');document.querySelectorAll('.dropdown-menu').forEach(m=>m.classList.remove('show'));"><i class="fas fa-trash text-red-400"></i> Delete</button>
                                    </div>
                                </div>
                            </td>
                        </tr>`;
                    } else {
                        let icon = f.name.endsWith('.zip') ? '<i class="fas fa-file-archive text-orange-400 mr-2"></i>' : '<i class="fas fa-file-code text-blue-400 mr-2"></i>';
                        let size = (f.size/1024).toFixed(1) + ' KB';
                        if (f.size > 1024*1024) size = (f.size/(1024*1024)).toFixed(1) + ' MB';
                        
                        let menuItems = '';
                        if (f.name.endsWith('.zip')) {
                            menuItems += `<button onclick="unzipFile('${f.name}');document.querySelectorAll('.dropdown-menu').forEach(m=>m.classList.remove('show'));"><i class="fas fa-file-archive text-green-400"></i> Extract</button>`;
                            menuItems += `<button onclick="renameFilePrompt('${f.name}');document.querySelectorAll('.dropdown-menu').forEach(m=>m.classList.remove('show'));"><i class="fas fa-i-cursor text-yellow-400"></i> Rename</button>`;
                            menuItems += `<button onclick="deleteFile('${f.name}');document.querySelectorAll('.dropdown-menu').forEach(m=>m.classList.remove('show'));"><i class="fas fa-trash text-red-400"></i> Delete</button>`;
                        } else {
                            menuItems += `<button onclick="editFile('${f.name}');document.querySelectorAll('.dropdown-menu').forEach(m=>m.classList.remove('show'));"><i class="fas fa-edit text-blue-400"></i> Edit</button>`;
                            menuItems += `<button onclick="renameFilePrompt('${f.name}');document.querySelectorAll('.dropdown-menu').forEach(m=>m.classList.remove('show'));"><i class="fas fa-i-cursor text-yellow-400"></i> Rename</button>`;
                            menuItems += `<button onclick="deleteFile('${f.name}');document.querySelectorAll('.dropdown-menu').forEach(m=>m.classList.remove('show'));"><i class="fas fa-trash text-red-400"></i> Delete</button>`;
                        }
                        
                        h += `<tr class="border-b border-gray-800 hover:bg-[#1a1d27]">
                            <td class="p-3">${icon}${f.name}</td>
                            <td class="p-3 text-gray-400 text-xs">${size}</td>
                            <td class="p-3 text-gray-500 text-xs">${f.modified}</td>
                            <td class="p-3 text-right">
                                <div class="dropdown">
                                    <button onclick="toggleDropdown(event, '${fileId}')" class="text-gray-400 hover:text-white px-2 py-1 rounded"><i class="fas fa-ellipsis-v"></i></button>
                                    <div class="dropdown-menu" id="dropdown-${fileId}">${menuItems}</div>
                                </div>
                            </td>
                        </tr>`;
                    }
                });
                h += '</tbody></table>';
                document.getElementById('fileList').innerHTML = h;
            });
        }

        function uploadFile() {
            let f = document.getElementById('fileInput').files[0];
            if(!f) return;
            
            if (f.size > MAX_FILE_SIZE) {
                showToast(`File too large! Max ${MAX_FILE_SIZE/(1024*1024)}MB allowed`, 'error', 4000);
                document.getElementById('fileInput').value = '';
                return;
            }
            
            let fd = new FormData(); fd.append('file', f);
            if (currentFolder) fd.append('folder', currentFolder);
            
            showToast('Uploading...', 'info', 0);
            fetch(`/api/upload/${currentServerId}`, {method:'POST', body:fd}).then(() => {
                loadFileList(currentFolder); document.getElementById('fileInput').value='';
                showToast('Uploaded!', 'success', 2000);
            });
        }

        function createFolder() { 
            const n = prompt('Folder name:'); 
            if(n) {
                const folderPath = currentFolder ? currentFolder + '/' + n : n;
                fetch(`/api/create_folder/${currentServerId}`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({foldername: folderPath})}).then(()=>loadFileList(currentFolder)); 
            }
        }
        
        function createFile() { 
            const n = prompt('File name:','newfile.py'); 
            if(n) {
                const filePath = currentFolder ? currentFolder + '/' + n : n;
                fetch(`/api/file/${currentServerId}`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({filename: filePath, content:'# New file'})}).then(()=>loadFileList(currentFolder)); 
            }
        }
        
        function unzipFile(filename) {
            const filePath = currentFolder ? currentFolder + '/' + filename : filename;
            showToast('Extracting...', 'info', 0);
            fetch(`/api/unzip/${currentServerId}`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({filename: filePath})})
            .then(r=>r.json()).then(data => {
                if(data.status === 'success') { showToast('Extracted!', 'success', 3000); loadFileList(currentFolder); }
                else showToast(data.msg || 'Failed!', 'error', 3000);
            });
        }
        
        function renameFilePrompt(oldName) {
            const newName = prompt('Enter new name:', oldName);
            if(newName && newName !== oldName) {
                const oldPath = currentFolder ? currentFolder + '/' + oldName : oldName;
                const newPath = currentFolder ? currentFolder + '/' + newName : newName;
                fetch(`/api/rename/${currentServerId}`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({old_name: oldPath, new_name: newPath})}).then(r=>r.json()).then(data => {
                    if(data.success) { showToast('Renamed!', 'success', 2000); loadFileList(currentFolder); }
                    else showToast(data.error || 'Failed!', 'error', 3000);
                });
            }
        }
        
        function editFile(filename) {
            editingFile = currentFolder ? currentFolder + '/' + filename : filename;
            fetch(`/api/file/${currentServerId}?filename=${encodeURIComponent(editingFile)}`).then(r=>r.json()).then(data => {
                document.getElementById('editor').value = data.content || '';
                document.getElementById('editTitle').innerHTML = `<i class="fas fa-edit mr-2"></i>${editingFile}`;
                document.getElementById('editModal').classList.remove('hidden');
            });
        }
        
        function saveFileContent() {
            fetch(`/api/file/${currentServerId}`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({filename:editingFile, content:document.getElementById('editor').value})}).then(()=>{closeEdit();loadFileList(currentFolder);showToast('Saved!','success',2000);});
        }
        
        function deleteFile(filename) {
            const filePath = currentFolder ? currentFolder + '/' + filename : filename;
            if(confirm(`Delete "${filename}"?`)) {
                fetch(`/api/file/${currentServerId}`, {method:'DELETE', headers:{'Content-Type':'application/json'}, body:JSON.stringify({filename: filePath})}).then(()=>loadFileList(currentFolder));
            }
        }
        
        function closeEdit() { document.getElementById('editModal').classList.add('hidden'); editingFile=null; }
        
        function loadStartup() {
            fetch(`/api/get_startup/${currentServerId}`).then(r=>r.json()).then(data => {
                document.getElementById('mainFileInput').value = data.main_file||'main.py';
                document.getElementById('requirementsInput').value = data.requirements_file||'';
            });
        }
        
        function saveStartup() {
            fetch(`/api/set_startup/${currentServerId}`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({main_file:document.getElementById('mainFileInput').value.trim(), requirements_file:document.getElementById('requirementsInput').value.trim()||null})}).then(()=>showToast('Saved!','success',2000));
        }

        // ============ GITHUB ============
        function deployRepo(isPrivate) {
            const repoUrl = isPrivate ? document.getElementById('privateRepoUrl').value.trim() : document.getElementById('publicRepoUrl').value.trim();
            const accessToken = isPrivate ? document.getElementById('accessToken').value.trim() : '';
            
            if (!repoUrl) {
                showToast('Repository URL is required!', 'error');
                return;
            }
            
            if (isPrivate && !accessToken) {
                showToast('Access token is required for private repos!', 'error');
                return;
            }
            
            showToast('Starting deployment...', 'info', 0);
            
            fetch(`/api/github/deploy/${currentServerId}`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    repo_url: repoUrl,
                    access_token: accessToken,
                    is_private: isPrivate
                })
            })
            .then(r => r.json())
            .then(d => {
                if (d.status === 'success') {
                    showToast('Deployment started! Check terminal.', 'success');
                    loadGitLogs();
                    setTimeout(() => {
                        if (activeTab === 'files') loadFileList(currentFolder);
                    }, 5000);
                } else {
                    showToast(d.msg || 'Deployment failed!', 'error');
                }
            })
            .catch(() => showToast('Connection failed!', 'error'));
        }
        
        function loadGitLogs() {
            fetch(`/api/github/logs/${currentServerId}`)
                .then(r => r.json())
                .then(d => {
                    const el = document.getElementById('gitLogs');
                    if (el) {
                        el.innerHTML = d.logs || '> Ready for deployment...';
                        el.scrollTop = el.scrollHeight;
                    }
                });
        }
        
        function clearGitLogs() {
            fetch(`/api/github/clear_logs/${currentServerId}`, {method: 'POST'})
                .then(() => {
                    document.getElementById('gitLogs').innerHTML = '> Terminal cleared. Ready for deployment...';
                    showToast('Terminal cleared!', 'success', 2000);
                });
        }

        // ============ PROFILE ============
        function updateCredentials() {
            const newPass = document.getElementById('newPassword').value;
            const confirm = document.getElementById('confirmPassword').value;
            
            if (!newPass || !confirm) {
                showToast('Please fill all password fields!', 'error');
                return;
            }
            if (newPass !== confirm) {
                showToast('Passwords do not match!', 'error');
                return;
            }
            if (newPass.length < 4) {
                showToast('Password must be at least 4 characters!', 'error');
                return;
            }
            
            showToast('Updating credentials...', 'info', 0);
            
            fetch(`/api/change_password/${currentServerId}`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    current_password: '',
                    new_password: newPass
                })
            })
            .then(r => r.json())
            .then(d => {
                if (d.success || d.msg) {
                    showToast('Password updated successfully!', 'success');
                    document.getElementById('newPassword').value = '';
                    document.getElementById('confirmPassword').value = '';
                } else {
                    showToast(d.error || 'Update failed!', 'error');
                }
            })
            .catch(() => showToast('Connection failed!', 'error'));
        }

        loadLogs(); updateStats();
        setInterval(() => { if(activeTab==='console') loadLogs(); updateStats(); if(activeTab==='github') loadGitLogs(); }, 3000);
    </script>
</body>
</html>''',

'landing.html': r'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>SADNAN HOSTING - Free Python Bot Hosting</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css">
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Poppins:wght@300;400;500;600;700;800;900&display=swap');
        
        * { font-family: 'Poppins', sans-serif; }
        
        body { background: #0a0c14; color: #fff; overflow-x: hidden; }
        
        .gradient-text {
            background: linear-gradient(135deg, #3b82f6, #8b5cf6);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        
        .hero-glow {
            background: radial-gradient(circle at center, rgba(59,130,246,0.15) 0%, transparent 70%);
        }
        
        .card {
            background: rgba(18,20,28,0.8);
            border: 1px solid rgba(59,130,246,0.2);
            border-radius: 16px;
            transition: all 0.3s ease;
        }
        .card:hover {
            border-color: rgba(59,130,246,0.5);
            transform: translateY(-2px);
            box-shadow: 0 20px 40px rgba(59,130,246,0.1);
        }
        
        .btn-blue {
            background: linear-gradient(135deg, #3b82f6, #2563eb);
            border: none;
            padding: 12px 28px;
            border-radius: 10px;
            font-weight: 600;
            transition: all 0.3s ease;
            display: inline-flex;
            align-items: center;
            gap: 8px;
            cursor: pointer;
            text-decoration: none;
            color: white;
        }
        .btn-blue:hover {
            transform: translateY(-2px);
            box-shadow: 0 10px 30px rgba(59,130,246,0.3);
        }
        
        .btn-dark {
            background: #1a1d27;
            border: 2px solid #2a2d37;
            padding: 12px 28px;
            border-radius: 10px;
            font-weight: 600;
            transition: all 0.3s ease;
            display: inline-flex;
            align-items: center;
            gap: 8px;
            cursor: pointer;
            text-decoration: none;
            color: white;
        }
        .btn-dark:hover {
            background: #252836;
            border-color: #3b82f6;
        }
        
        .stat-number {
            font-size: 2rem;
            font-weight: 800;
            background: linear-gradient(135deg, #3b82f6, #60a5fa);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        @media (min-width: 768px) {
            .stat-number { font-size: 2.5rem; }
        }
        
        .feature-icon {
            width: 45px;
            height: 45px;
            border-radius: 12px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 20px;
        }
        @media (min-width: 768px) {
            .feature-icon { width: 50px; height: 50px; font-size: 22px; }
        }
        
        .pricing-card {
            border: 1px solid rgba(59,130,246,0.3);
            background: rgba(18,20,28,0.9);
            border-radius: 20px;
        }
        .pricing-card.popular {
            border-color: #3b82f6;
            background: rgba(59,130,246,0.08);
            position: relative;
        }
        .popular-badge {
            position: absolute;
            top: -14px;
            left: 50%;
            transform: translateX(-50%);
            background: #3b82f6;
            color: white;
            padding: 5px 20px;
            border-radius: 20px;
            font-size: 11px;
            font-weight: 600;
            white-space: nowrap;
        }
        
        @keyframes float {
            0%, 100% { transform: translateY(0); }
            50% { transform: translateY(-8px); }
        }
        .float { animation: float 3s ease-in-out infinite; }
        
        /* Smooth scroll */
        html { scroll-behavior: smooth; }
    </style>
</head>
<body>
    
    <!-- Navbar -->
    <nav class="fixed top-0 w-full z-50 bg-[#0a0c14]/90 backdrop-blur-md border-b border-gray-800">
        <div class="container mx-auto px-4 md:px-6 py-3 md:py-4 flex justify-between items-center">
            <div class="flex items-center gap-2 md:gap-3">
                <div class="w-8 h-8 md:w-10 md:h-10 bg-gradient-to-br from-blue-600 to-blue-400 rounded-lg md:rounded-xl flex items-center justify-center">
                    <i class="fas fa-cloud-upload-alt text-white text-sm md:text-base"></i>
                </div>
                <span class="text-lg md:text-xl font-bold">SADNAN <span class="text-blue-400">HOSTING</span></span>
            </div>
            <div class="hidden lg:flex gap-6 text-sm">
                <a href="#features" class="text-gray-400 hover:text-white transition">Features</a>
                <a href="#pricing" class="text-gray-400 hover:text-white transition">Pricing</a>
                <a href="#how" class="text-gray-400 hover:text-white transition">How It Works</a>
                <a href="#faq" class="text-gray-400 hover:text-white transition">FAQ</a>
            </div>
            <a href="https://t.me/zip_extrctor_bot" target="_blank" class="btn-blue text-xs md:text-sm !py-2 !px-4 md:!py-3 md:!px-6">
                <i class="fab fa-telegram-plane"></i> <span class="hidden sm:inline">Get Started Free</span>
            </a>
        </div>
    </nav>

    <!-- Hero Section -->
    <section class="pt-24 md:pt-32 pb-16 md:pb-20 px-4 md:px-6 hero-glow">
        <div class="container mx-auto text-center max-w-4xl">
            <div class="inline-block bg-blue-500/10 border border-blue-500/30 rounded-full px-3 md:px-4 py-1 md:py-1.5 text-xs text-blue-400 mb-4 md:mb-6">
                🚀 Free Python Bot Hosting Platform
            </div>
            <h1 class="text-3xl md:text-5xl lg:text-6xl font-extrabold mb-4 md:mb-6 leading-tight">
                Host Your <span class="gradient-text">Python Bots</span><br>For Free Forever!
            </h1>
            <p class="text-gray-400 text-sm md:text-lg lg:text-xl mb-8 md:mb-10 max-w-2xl mx-auto px-2">
                The easiest way to host your Telegram/Discord bots. Get a free panel with 5 referrals or use a redeem code. 24/7 uptime, instant setup!
            </p>
            <div class="flex flex-col sm:flex-row gap-3 md:gap-4 justify-center items-center">
                <a href="https://t.me/zip_extrctor_bot" target="_blank" class="btn-blue text-sm md:text-base w-full sm:w-auto justify-center">
                    <i class="fab fa-telegram-plane"></i> Get Started Free
                </a>
                <a href="https://t.me/YOUR_TELEGRAM" target="_blank" class="btn-dark text-sm md:text-base w-full sm:w-auto justify-center">
                    <i class="fab fa-telegram"></i> Contact Developer
                </a>
            </div>
            
            <!-- Stats -->
            <div class="grid grid-cols-3 gap-4 md:gap-6 mt-12 md:mt-16 max-w-lg mx-auto">
                <div>
                    <div class="stat-number">500+</div>
                    <div class="text-gray-500 text-[10px] md:text-xs">Active Panels</div>
                </div>
                <div>
                    <div class="stat-number">24/7</div>
                    <div class="text-gray-500 text-[10px] md:text-xs">Uptime</div>
                </div>
                <div>
                    <div class="stat-number">Free</div>
                    <div class="text-gray-500 text-[10px] md:text-xs">Forever</div>
                </div>
            </div>
        </div>
    </section>

    <!-- Features -->
    <section id="features" class="py-16 md:py-20 px-4 md:px-6">
        <div class="container mx-auto max-w-6xl">
            <div class="text-center mb-12 md:mb-16">
                <h2 class="text-2xl md:text-4xl font-bold mb-3 md:mb-4">Why Choose <span class="gradient-text">SADNAN HOSTING</span>?</h2>
                <p class="text-gray-400 text-sm md:text-base">Everything you need to host your Python bots</p>
            </div>
            
            <div class="grid sm:grid-cols-2 lg:grid-cols-3 gap-4 md:gap-6">
                <div class="card p-4 md:p-6">
                    <div class="feature-icon bg-blue-500/20 text-blue-400 mb-3 md:mb-4"><i class="fas fa-bolt"></i></div>
                    <h3 class="font-bold text-base md:text-lg mb-1 md:mb-2">Instant Setup</h3>
                    <p class="text-gray-400 text-xs md:text-sm">Create your panel in seconds. No complex configuration needed.</p>
                </div>
                <div class="card p-4 md:p-6">
                    <div class="feature-icon bg-green-500/20 text-green-400 mb-3 md:mb-4"><i class="fas fa-shield-halved"></i></div>
                    <h3 class="font-bold text-base md:text-lg mb-1 md:mb-2">24/7 Uptime</h3>
                    <p class="text-gray-400 text-xs md:text-sm">Your bots run around the clock with automatic crash recovery.</p>
                </div>
                <div class="card p-4 md:p-6">
                    <div class="feature-icon bg-purple-500/20 text-purple-400 mb-3 md:mb-4"><i class="fas fa-gift"></i></div>
                    <h3 class="font-bold text-base md:text-lg mb-1 md:mb-2">Referral Rewards</h3>
                    <p class="text-gray-400 text-xs md:text-sm">Get a FREE panel for every 5 referrals. Unlimited rewards!</p>
                </div>
                <div class="card p-4 md:p-6">
                    <div class="feature-icon bg-yellow-500/20 text-yellow-400 mb-3 md:mb-4"><i class="fas fa-file-code"></i></div>
                    <h3 class="font-bold text-base md:text-lg mb-1 md:mb-2">File Manager</h3>
                    <p class="text-gray-400 text-xs md:text-sm">Upload, edit, rename files directly from browser with ZIP support.</p>
                </div>
                <div class="card p-4 md:p-6">
                    <div class="feature-icon bg-red-500/20 text-red-400 mb-3 md:mb-4"><i class="fas fa-terminal"></i></div>
                    <h3 class="font-bold text-base md:text-lg mb-1 md:mb-2">Web Console</h3>
                    <p class="text-gray-400 text-xs md:text-sm">Real-time log viewer. See your bot output instantly.</p>
                </div>
                <div class="card p-4 md:p-6">
                    <div class="feature-icon bg-cyan-500/20 text-cyan-400 mb-3 md:mb-4"><i class="fas fa-gauge"></i></div>
                    <h3 class="font-bold text-base md:text-lg mb-1 md:mb-2">Resource Monitor</h3>
                    <p class="text-gray-400 text-xs md:text-sm">Track CPU, RAM, and Network usage in real-time.</p>
                </div>
            </div>
        </div>
    </section>

    <!-- How It Works -->
    <section id="how" class="py-16 md:py-20 px-4 md:px-6 bg-[#0e1018]">
        <div class="container mx-auto max-w-4xl text-center">
            <h2 class="text-2xl md:text-4xl font-bold mb-12 md:mb-16">How It <span class="gradient-text">Works</span></h2>
            <div class="grid grid-cols-1 sm:grid-cols-3 gap-6 md:gap-8">
                <div>
                    <div class="w-14 h-14 md:w-16 md:h-16 bg-blue-600 rounded-2xl flex items-center justify-center text-xl md:text-2xl font-bold mx-auto mb-3 md:mb-4">1</div>
                    <h3 class="font-bold text-base md:text-lg mb-1 md:mb-2">Join Telegram Bot</h3>
                    <p class="text-gray-400 text-xs md:text-sm">Start our Telegram bot and verify your account.</p>
                </div>
                <div>
                    <div class="w-14 h-14 md:w-16 md:h-16 bg-blue-600 rounded-2xl flex items-center justify-center text-xl md:text-2xl font-bold mx-auto mb-3 md:mb-4">2</div>
                    <h3 class="font-bold text-base md:text-lg mb-1 md:mb-2">Share Referral Link</h3>
                    <p class="text-gray-400 text-xs md:text-sm">Get 5 people to join using your referral link.</p>
                </div>
                <div>
                    <div class="w-14 h-14 md:w-16 md:h-16 bg-blue-600 rounded-2xl flex items-center justify-center text-xl md:text-2xl font-bold mx-auto mb-3 md:mb-4">3</div>
                    <h3 class="font-bold text-base md:text-lg mb-1 md:mb-2">Claim Free Panel!</h3>
                    <p class="text-gray-400 text-xs md:text-sm">Create your panel and start hosting instantly!</p>
                </div>
            </div>
        </div>
    </section>

    <!-- Pricing -->
    <section id="pricing" class="py-16 md:py-20 px-4 md:px-6">
        <div class="container mx-auto max-w-5xl">
            <div class="text-center mb-12 md:mb-16">
                <h2 class="text-2xl md:text-4xl font-bold mb-3 md:mb-4">Simple <span class="gradient-text">Pricing</span></h2>
                <p class="text-gray-400 text-sm md:text-base">Get started for free, upgrade when needed</p>
            </div>
            
            <div class="grid sm:grid-cols-2 lg:grid-cols-3 gap-4 md:gap-6">
                <!-- Free -->
                <div class="pricing-card p-6 md:p-8 text-center">
                    <div class="text-3xl md:text-4xl mb-3 md:mb-4">🆓</div>
                    <h3 class="font-bold text-lg md:text-xl mb-1 md:mb-2">Free</h3>
                    <p class="text-gray-400 text-xs md:text-sm mb-4 md:mb-6">Via Referrals</p>
                    <div class="text-3xl md:text-4xl font-extrabold mb-4 md:mb-6">$0</div>
                    <ul class="text-xs md:text-sm text-gray-400 space-y-2 md:space-y-3 mb-6 md:mb-8 text-left">
                        <li><i class="fas fa-check text-green-400 mr-2"></i> 5 Referrals Required</li>
                        <li><i class="fas fa-check text-green-400 mr-2"></i> 3 Days Validity</li>
                        <li><i class="fas fa-check text-green-400 mr-2"></i> 1GB RAM / 1GB Disk</li>
                        <li><i class="fas fa-check text-green-400 mr-2"></i> 30% CPU Limit</li>
                    </ul>
                    <a href="https://t.me/zip_extrctor_bot" target="_blank" class="btn-dark w-full justify-center text-sm md:text-base">Get Free</a>
                </div>
                
                <!-- Popular -->
                <div class="pricing-card popular p-6 md:p-8 text-center">
                    <div class="popular-badge">🔥 Most Popular</div>
                    <div class="text-3xl md:text-4xl mb-3 md:mb-4">⭐</div>
                    <h3 class="font-bold text-lg md:text-xl mb-1 md:mb-2">Standard</h3>
                    <p class="text-gray-400 text-xs md:text-sm mb-4 md:mb-6">7 Days Panel</p>
                    <div class="text-3xl md:text-4xl font-extrabold mb-4 md:mb-6">$2</div>
                    <ul class="text-xs md:text-sm text-gray-400 space-y-2 md:space-y-3 mb-6 md:mb-8 text-left">
                        <li><i class="fas fa-check text-green-400 mr-2"></i> No Referrals Needed</li>
                        <li><i class="fas fa-check text-green-400 mr-2"></i> 7 Days Validity</li>
                        <li><i class="fas fa-check text-green-400 mr-2"></i> 1GB RAM / 1GB Disk</li>
                        <li><i class="fas fa-check text-green-400 mr-2"></i> 50% CPU Limit</li>
                    </ul>
                    <a href="https://t.me/YOUR_TELEGRAM" target="_blank" class="btn-blue w-full justify-center text-sm md:text-base">Buy Now</a>
                </div>
                
                <!-- Premium -->
                <div class="pricing-card p-6 md:p-8 text-center sm:col-span-2 lg:col-span-1">
                    <div class="text-3xl md:text-4xl mb-3 md:mb-4">💎</div>
                    <h3 class="font-bold text-lg md:text-xl mb-1 md:mb-2">Premium</h3>
                    <p class="text-gray-400 text-xs md:text-sm mb-4 md:mb-6">30 Days Panel</p>
                    <div class="text-3xl md:text-4xl font-extrabold mb-4 md:mb-6">$5</div>
                    <ul class="text-xs md:text-sm text-gray-400 space-y-2 md:space-y-3 mb-6 md:mb-8 text-left">
                        <li><i class="fas fa-check text-green-400 mr-2"></i> No Referrals Needed</li>
                        <li><i class="fas fa-check text-green-400 mr-2"></i> 30 Days Validity</li>
                        <li><i class="fas fa-check text-green-400 mr-2"></i> 2GB RAM / 2GB Disk</li>
                        <li><i class="fas fa-check text-green-400 mr-2"></i> 80% CPU Limit</li>
                    </ul>
                    <a href="https://t.me/YOUR_TELEGRAM" target="_blank" class="btn-blue w-full justify-center text-sm md:text-base">Buy Now</a>
                </div>
            </div>
        </div>
    </section>

    <!-- FAQ -->
    <section id="faq" class="py-16 md:py-20 px-4 md:px-6 bg-[#0e1018]">
        <div class="container mx-auto max-w-3xl">
            <div class="text-center mb-12 md:mb-16">
                <h2 class="text-2xl md:text-4xl font-bold mb-3 md:mb-4">Frequently <span class="gradient-text">Asked Questions</span></h2>
            </div>
            <div class="space-y-3 md:space-y-4">
                <div class="card p-4 md:p-6">
                    <h3 class="font-bold text-sm md:text-base mb-1 md:mb-2">🤔 How do I get a free panel?</h3>
                    <p class="text-gray-400 text-xs md:text-sm">Share your referral link with 5 friends. When they join our Telegram bot using your link, you get a free panel!</p>
                </div>
                <div class="card p-4 md:p-6">
                    <h3 class="font-bold text-sm md:text-base mb-1 md:mb-2">⚡ How fast is the setup?</h3>
                    <p class="text-gray-400 text-xs md:text-sm">Instant! As soon as you create a panel, it's ready to use. Just upload your bot file and click Start.</p>
                </div>
                <div class="card p-4 md:p-6">
                    <h3 class="font-bold text-sm md:text-base mb-1 md:mb-2">🔄 What happens after expiry?</h3>
                    <p class="text-gray-400 text-xs md:text-sm">Your panel expires. But you can get another one by referring 5 more people! Or buy a longer plan.</p>
                </div>
                <div class="card p-4 md:p-6">
                    <h3 class="font-bold text-sm md:text-base mb-1 md:mb-2">🐍 What Python version?</h3>
                    <p class="text-gray-400 text-xs md:text-sm">Python 3.12+ with auto pip install support. All your requirements.txt packages will be installed automatically!</p>
                </div>
            </div>
        </div>
    </section>

    <!-- CTA -->
    <section class="py-16 md:py-20 px-4 md:px-6 text-center">
        <div class="container mx-auto max-w-3xl">
            <h2 class="text-2xl md:text-4xl font-bold mb-4 md:mb-6">Ready to <span class="gradient-text">Get Started</span>?</h2>
            <p class="text-gray-400 text-sm md:text-base mb-8 md:mb-10">Join hundreds of developers hosting their bots for free!</p>
            <div class="flex flex-col sm:flex-row gap-3 md:gap-4 justify-center">
                <a href="https://t.me/zip_extrctor_bot" target="_blank" class="btn-blue text-sm md:text-base w-full sm:w-auto justify-center">
                    <i class="fab fa-telegram-plane"></i> Get Started Free
                </a>
                <a href="https://t.me/YOUR_TELEGRAM" target="_blank" class="btn-dark text-sm md:text-base w-full sm:w-auto justify-center">
                    <i class="fab fa-telegram"></i> Contact Developer
                </a>
            </div>
        </div>
    </section>

    <!-- Footer -->
    <footer class="border-t border-gray-800 py-8 md:py-10 px-4 md:px-6">
        <div class="container mx-auto max-w-6xl">
            <div class="grid grid-cols-2 md:grid-cols-3 gap-6 md:gap-8 mb-6 md:mb-8">
                <div class="col-span-2 md:col-span-1">
                    <div class="flex items-center gap-2 md:gap-3 mb-3 md:mb-4">
                        <div class="w-8 h-8 md:w-10 md:h-10 bg-gradient-to-br from-blue-600 to-blue-400 rounded-lg md:rounded-xl flex items-center justify-center">
                            <i class="fas fa-cloud-upload-alt text-white text-sm md:text-base"></i>
                        </div>
                        <span class="text-lg md:text-xl font-bold">SADNAN <span class="text-blue-400">HOSTING</span></span>
                    </div>
                    <p class="text-gray-500 text-xs md:text-sm">Free Python Bot Hosting Platform. Made with ❤️ in Bangladesh.</p>
                </div>
                <div>
                    <h4 class="font-bold text-sm md:text-base mb-3 md:mb-4">Quick Links</h4>
                    <ul class="text-gray-500 text-xs md:text-sm space-y-1.5 md:space-y-2">
                        <li><a href="https://t.me/zip_extrctor_bot" target="_blank" class="hover:text-blue-400">Telegram Bot</a></li>
                        <li><a href="https://t.me/YOUR_TELEGRAM" target="_blank" class="hover:text-blue-400">Support</a></li>
                        <li><a href="https://t.me/YOUR_TELEGRAM" target="_blank" class="hover:text-blue-400">Channel</a></li>
                        <li><a href="https://t.me/YOUR_TELEGRAM" target="_blank" class="hover:text-blue-400">Group</a></li>
                    </ul>
                </div>
                <div>
                    <h4 class="font-bold text-sm md:text-base mb-3 md:mb-4">Contact</h4>
                    <ul class="text-gray-500 text-xs md:text-sm space-y-1.5 md:space-y-2">
                        <li><i class="fab fa-telegram-plane mr-2"></i> @YOUR_TELEGRAM</li>
                        <li><i class="fas fa-globe mr-2"></i> sadnan-vps-hosting.onrender.com</li>
                    </ul>
                </div>
            </div>
            <div class="border-t border-gray-800 pt-4 md:pt-6 text-center text-gray-600 text-[10px] md:text-xs">
                &copy; 2026 SADNAN HOSTING. All rights reserved.
            </div>
        </div>
    </footer>

</body>
</html>''',

'login.html': r'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Login | SADNAN HOSTING</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css">
</head>
<body class="bg-[#0a0c12] min-h-screen flex items-center justify-center p-4">
    <div class="bg-[#12141c] rounded-2xl p-8 w-full max-w-md border border-blue-500/30 shadow-2xl">
        <div class="text-center mb-8">
            <div class="w-20 h-20 bg-gradient-to-tr from-blue-600 to-blue-400 rounded-2xl flex items-center justify-center mx-auto mb-4">
                <i class="fas fa-cloud-upload-alt text-4xl text-white"></i>
            </div>
            <h1 class="text-3xl font-bold text-white">SADNAN <span class="text-blue-400">HOSTING</span></h1>
            <p class="text-gray-400 text-sm mt-1">Premium Hosting Solution</p>
        </div>
        
        {% if error %}
        <div class="bg-red-500/20 border border-red-500 rounded-lg p-3 mb-4 text-red-400 text-sm text-center">
            <i class="fas fa-exclamation-triangle mr-2"></i> {{ error }}
        </div>
        {% endif %}
        
        <form method="POST">
            <div class="mb-4">
                <label class="block text-gray-400 text-sm mb-2">Username</label>
                <input type="text" name="username" autocomplete="username" required 
                    class="w-full bg-[#0c0e14] border border-gray-700 rounded-lg px-4 py-3 text-white focus:border-blue-500 focus:outline-none transition">
            </div>
            <div class="mb-6">
                <label class="block text-gray-400 text-sm mb-2">Password</label>
                <input type="password" name="password" autocomplete="current-password" required
                    class="w-full bg-[#0c0e14] border border-gray-700 rounded-lg px-4 py-3 text-white focus:border-blue-500 focus:outline-none transition">
            </div>
            <button type="submit" 
                class="w-full bg-gradient-to-r from-blue-600 to-blue-500 hover:from-blue-500 hover:to-blue-600 text-white font-bold py-3 rounded-lg transition transform hover:scale-105">
                <i class="fas fa-sign-in-alt mr-2"></i> Login
            </button>
        </form>
        
        <div class="text-center mt-6">
            <p class="text-gray-600 text-xs">
                <i class="fas fa-shield-alt"></i> Secure Access Only
            </p>
        </div>
    </div>
</body>
</html>''',

}

app.jinja_loader = DictLoader(TEMPLATES)

if __name__ == '__main__':
    threading.Thread(target=resume_all, daemon=True).start()
    threading.Thread(target=auto_ping, daemon=True).start()
    print("\n" + "=" * 50)
    print("🚀 SADNAN HOSTING - FINAL")
    print("=" * 50)
    print("📍 Landing: http://localhost:5000")
    print("📍 Admin: http://localhost:5000/login")
    print("🔗 API: http://localhost:5000/api/create")
    print("👤 admin / admin123")
    print("=" * 50 + "\n")
    app.run(debug=False, host='0.0.0.0', port=10000)  # Render default port