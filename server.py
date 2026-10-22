import os, sqlite3, secrets, hashlib, hmac, io, json, socket
from datetime import datetime, timezone
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request, Response, UploadFile, File, Form
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from PIL import Image, ImageDraw, ImageFont

BASE=Path(__file__).resolve().parent
DB=Path(os.getenv('WING_DB_PATH', str(BASE/'data'/'wingops.db')))
DB.parent.mkdir(parents=True,exist_ok=True)
ADMIN_PASSWORD=os.getenv('ADMIN_PASSWORD','')
if not ADMIN_PASSWORD or len(ADMIN_PASSWORD)<10:
 raise RuntimeError('Set ADMIN_PASSWORD to a strong password (10+ characters) before starting the server')
SECRET=os.getenv('SESSION_SECRET',secrets.token_hex(32))
app=FastAPI(title='WingOps')
app.mount('/static',StaticFiles(directory=BASE/'static'),name='static')
TASKS=[('opening','Turn on lights, hood and equipment'),('opening','Check and log refrigerator/freezer temperatures'),('opening','Check handwash sink, soap and paper towels'),('opening','Sanitize prep tables and utensils'),('opening','Check food labels, dates and storage order'),('opening','Prepare sauces, packaging and supplies'),('opening','Verify POS, delivery tablets and printers'),('opening','Check dining room, restrooms and trash'),('opening','Confirm fryers are ready and safe'),('opening','Take opening station photo (optional)')]

def conn():
 c=sqlite3.connect(DB);c.row_factory=sqlite3.Row;return c

def init():
 with conn() as c:
  c.executescript('''CREATE TABLE IF NOT EXISTS employees(id INTEGER PRIMARY KEY, name TEXT NOT NULL, pin_hash TEXT NOT NULL, store TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, employee_id INTEGER NOT NULL, clock_in TEXT NOT NULL, clock_out TEXT);
CREATE TABLE IF NOT EXISTS submissions(id INTEGER PRIMARY KEY AUTOINCREMENT, employee_id INTEGER NOT NULL, store TEXT NOT NULL, submitted_at TEXT NOT NULL, payload TEXT NOT NULL, image_file TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT, time TEXT NOT NULL, employee_id INTEGER, action TEXT NOT NULL, details TEXT);
CREATE TABLE IF NOT EXISTS checklist_config(id INTEGER PRIMARY KEY CHECK(id=1), tasks_json TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1);
''')
  if c.execute('SELECT COUNT(*) FROM checklist_config').fetchone()[0]==0:
   c.execute('INSERT INTO checklist_config(id,tasks_json,revision) VALUES(1,?,1)',(json.dumps([t[1] for t in TASKS]),))
  if os.getenv('DEMO_MODE')=='1' and c.execute('SELECT COUNT(*) FROM employees').fetchone()[0]==0:
   c.execute('INSERT INTO employees(name,pin_hash,store) VALUES(?,?,?)',('Demo Employee',pin_hash('1234'),'BQ Wings'))
  cols=[r[1] for r in c.execute('PRAGMA table_info(submissions)')]
  if 'photo_file' not in cols:c.execute('ALTER TABLE submissions ADD COLUMN photo_file TEXT')
def pin_hash(pin):return hashlib.pbkdf2_hmac('sha256',pin.encode(),b'wingops-demo-pin-v1',200000).hex()

init()
def now():return datetime.now(timezone.utc).isoformat()
def audit(c,employee,action,details=''):c.execute('INSERT INTO audit(time,employee_id,action,details) VALUES(?,?,?,?)',(now(),employee,action,details))
def require_admin(req):
 token=req.cookies.get('wing_admin')
 if not token or not hmac.compare_digest(token,hashlib.sha256((SECRET+ADMIN_PASSWORD).encode()).hexdigest()):raise HTTPException(401,'Admin login required')
def employee(req):
 token=req.headers.get('X-Employee-Token','')
 with conn() as c:
  row=c.execute('SELECT e.id,e.name,e.store,s.clock_in FROM sessions s JOIN employees e ON s.employee_id=e.id WHERE s.token=? AND s.clock_out IS NULL AND e.active=1',(token,)).fetchone()
 if not row:raise HTTPException(401,'Please log in')
 return dict(row)
class Login(BaseModel):
 employee_id:int
 pin:str
class AdminLogin(BaseModel):password:str
class Submit(BaseModel):checks:list[bool];notes:str='';revision:int=1
class ChecklistEdit(BaseModel):tasks:list[str];revision:int
class NewEmployee(BaseModel):name:str;pin:str;store:str='BQ Wings'
@app.get('/')
def index():return FileResponse(BASE/'static'/'index.html')
@app.get('/admin')
def admin():return FileResponse(BASE/'static'/'admin.html')
def current_checklist():
 with conn() as c:
  row=c.execute('SELECT tasks_json,revision FROM checklist_config WHERE id=1').fetchone()
 return {'tasks':json.loads(row['tasks_json']),'revision':row['revision']}
@app.get('/api/config')
def config():return current_checklist()
@app.get('/api/admin/checklist')
def admin_checklist(req:Request):
 require_admin(req)
 return current_checklist()
@app.put('/api/admin/checklist')
def save_checklist(req:Request,data:ChecklistEdit):
 require_admin(req)
 tasks=[x.strip() for x in data.tasks]
 if not 1<=len(tasks)<=40 or any(not t or len(t)>200 for t in tasks):raise HTTPException(400,'Use 1-40 nonempty items, maximum 200 characters each')
 if len(set(tasks))!=len(tasks):raise HTTPException(400,'Duplicate checklist items are not allowed')
 with conn() as c:
  cur=c.execute('UPDATE checklist_config SET tasks_json=?,revision=revision+1 WHERE id=1 AND revision=?',(json.dumps(tasks,ensure_ascii=False),data.revision))
  if cur.rowcount==0:raise HTTPException(409,'Another admin changed this list. Reload before saving.')
  audit(c,None,'checklist_update',f'revision {data.revision+1}; items {len(tasks)}')
 return {'ok':True,'revision':data.revision+1,'tasks':tasks}
@app.get('/install')
def install_page():return FileResponse(BASE/'static'/'install.html')

@app.post('/api/login')
def login(data:Login):
 with conn() as c:
  e=c.execute('SELECT * FROM employees WHERE id=? AND active=1',(data.employee_id,)).fetchone()
  if not e or not hmac.compare_digest(e['pin_hash'],pin_hash(data.pin)):raise HTTPException(401,'Invalid employee ID or PIN')
  existing=c.execute('SELECT token,clock_in FROM sessions WHERE employee_id=? AND clock_out IS NULL ORDER BY clock_in DESC LIMIT 1',(e['id'],)).fetchone()
  if existing:return {'token':existing['token'],'name':e['name'],'store':e['store'],'clock_in':existing['clock_in'],'already_clocked_in':True}
  token=secrets.token_urlsafe(32);stamp=now()
  c.execute('INSERT INTO sessions(token,employee_id,clock_in) VALUES(?,?,?)',(token,e['id'],stamp));audit(c,e['id'],'clock_in')
 return {'token':token,'name':e['name'],'store':e['store'],'clock_in':stamp,'already_clocked_in':False}
@app.get('/api/me')
def me(req:Request):return employee(req)
@app.post('/api/clock-out')
def clockout(req:Request):
 e=employee(req);token=req.headers.get('X-Employee-Token','')
 with conn() as c:c.execute('UPDATE sessions SET clock_out=? WHERE token=?',(now(),token));audit(c,e['id'],'clock_out')
 return {'ok':True}

def make_image(e,checks,notes,stamp,task_names):
 w=1100;h=320+len(checks)*64+min(len(notes),350)//55*30
 im=Image.new('RGB',(w,h),'#f7f9fc');d=ImageDraw.Draw(im)
 font='/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
 def f(s):return ImageFont.truetype(font,s) if Path(font).exists() else ImageFont.load_default()
 d.rectangle((0,0,w,120),fill='#13263c');d.text((40,27),'WINGOPS | OPENING REPORT',font=f(32),fill='white')
 d.text((40,80),f"Store: {e['store']}   Employee: {e['name']} (#{e['id']})",font=f(18),fill='#cde4ff')
 d.text((40,140),f'Submitted UTC: {stamp}',font=f(18),fill='#28384c')
 y=195
 for task,checked in zip(task_names,checks):
  d.rounded_rectangle((35,y,1065,y+52),radius=9,fill='#e1f4e8' if checked else '#ffe8e8')
  d.text((55,y+12),('DONE  ' if checked else 'MISS  ')+task,font=f(18),fill='#1b3544');y+=64
 d.text((40,y+15),'Notes: '+(notes[:350] or 'None'),font=f(16),fill='#28384c')
 b=io.BytesIO();im.save(b,'PNG');return b.getvalue()
@app.post('/api/submit')
async def submit(req:Request, checks_json:str=Form(...), notes:str=Form(''), revision:int=Form(...), photo:UploadFile=File(...)):
 try:
  checks=json.loads(checks_json)
  data=Submit(checks=checks,notes=notes,revision=revision)
 except (ValueError,TypeError):raise HTTPException(400,'Invalid checklist data')
 if photo.content_type not in ('image/jpeg','image/png','image/webp'):
  raise HTTPException(400,'Take or select a JPG, PNG or WebP kitchen photo')
 raw=await photo.read(8*1024*1024+1)
 if len(raw)>8*1024*1024:raise HTTPException(413,'Photo too large (max 8 MB)')
 try:
  picture=Image.open(io.BytesIO(raw));picture.verify()
  picture=Image.open(io.BytesIO(raw));picture.thumbnail((1800,1800))
  if picture.mode!='RGB':picture=picture.convert('RGB')
 except Exception:raise HTTPException(400,'Invalid photo')
 e=employee(req)
 cfg=current_checklist()
 if data.revision!=cfg['revision']:raise HTTPException(409,'Checklist changed. Refresh this page and complete the latest list.')
 if len(data.checks)!=len(cfg['tasks']) or not all(x is True for x in data.checks):raise HTTPException(400,'Complete every item before submitting')
 stamp=now();payload={'checks':data.checks,'tasks':cfg['tasks'],'revision':cfg['revision'],'notes':data.notes[:1000],'clock_in':e['clock_in']}
 with conn() as c:
  row=c.execute('INSERT INTO submissions(employee_id,store,submitted_at,payload,image_file) VALUES(?,?,?,?,?)',(e['id'],e['store'],stamp,json.dumps(payload),''))
  sid=row.lastrowid;filename=f'opening_{sid}.png';folder=DB.parent/'reports';folder.mkdir(exist_ok=True)
  (folder/filename).write_bytes(make_image(e,data.checks,data.notes,stamp,cfg['tasks']))
  photo_filename=f'kitchen_{sid}.jpg'
  picture.save(folder/photo_filename,format='JPEG',quality=82,optimize=True)
  c.execute('UPDATE submissions SET image_file=?,photo_file=? WHERE id=?',(filename,photo_filename,sid));audit(c,e['id'],'opening_submit',str(sid))
 return {'ok':True,'submission_id':sid,'submitted_at':stamp}
@app.post('/api/admin/login')
def adminlogin(data:AdminLogin,response:Response):
 if not hmac.compare_digest(data.password,ADMIN_PASSWORD):raise HTTPException(401,'Incorrect password')
 token=hashlib.sha256((SECRET+ADMIN_PASSWORD).encode()).hexdigest()
 response.set_cookie('wing_admin',token,httponly=True,samesite='lax',secure=os.getenv('COOKIE_SECURE','0')=='1',max_age=43200)
 return {'ok':True}
@app.post('/api/admin/logout')
def adminlogout(response:Response):response.delete_cookie('wing_admin');return {'ok':True}
@app.get('/api/admin/submissions')
def submissions(req:Request):
 require_admin(req)
 with conn() as c:
  rows=c.execute('SELECT s.id,s.store,s.submitted_at,s.payload,s.photo_file,e.name,e.id employee_id FROM submissions s JOIN employees e ON e.id=s.employee_id ORDER BY s.id DESC LIMIT 250').fetchall()
 return [dict(r) for r in rows]
@app.get('/api/admin/attendance')
def attendance(req:Request):
 require_admin(req)
 with conn() as c:rows=c.execute('SELECT s.clock_in,s.clock_out,e.id employee_id,e.name,e.store FROM sessions s JOIN employees e ON e.id=s.employee_id ORDER BY s.clock_in DESC LIMIT 250').fetchall()
 return [dict(r) for r in rows]
@app.get('/api/admin/photo/{sid}')
def kitchen_photo(req:Request,sid:int):
 require_admin(req)
 with conn() as c:row=c.execute('SELECT photo_file FROM submissions WHERE id=?',(sid,)).fetchone()
 if not row or not row['photo_file']:raise HTTPException(404,'No photo for this report')
 return FileResponse(DB.parent/'reports'/row['photo_file'],media_type='image/jpeg')
@app.get('/api/admin/report/{sid}.png')
def report(req:Request,sid:int):
 require_admin(req)
 with conn() as c:row=c.execute('SELECT image_file FROM submissions WHERE id=?',(sid,)).fetchone()
 if not row:raise HTTPException(404)
 return FileResponse(DB.parent/'reports'/row['image_file'],media_type='image/png')
@app.get('/api/admin/employees')
def employees(req:Request):
 require_admin(req)
 with conn() as c:rows=c.execute('SELECT id,name,store,active FROM employees ORDER BY id').fetchall()
 return [dict(r) for r in rows]
@app.post('/api/admin/employees')
def add_employee(req:Request,data:NewEmployee):
 require_admin(req)
 if len(data.pin)<6 or len(data.pin)>12 or not data.pin.isdigit():raise HTTPException(400,'PIN must be 6-12 digits')
 if not data.name.strip() or not data.store.strip():raise HTTPException(400,'Name and store required')
 with conn() as c:
  cur=c.execute('INSERT INTO employees(name,pin_hash,store) VALUES(?,?,?)',(data.name.strip(),pin_hash(data.pin),data.store.strip()))
 return {'id':cur.lastrowid}
