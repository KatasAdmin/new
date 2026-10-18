import os, json, secrets, hashlib, hmac, math, time, asyncio
from datetime import datetime, timezone, timedelta
from contextlib import asynccontextmanager
from typing import Optional
from fastapi import FastAPI, HTTPException, Header, Depends
from urllib.parse import parse_qsl
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, ForeignKey, Text, select
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.exc import IntegrityError

if os.getenv('RAILWAY_ENVIRONMENT') and not os.getenv('DATABASE_URL'):
 raise RuntimeError('DATABASE_URL не настроен. Подключи существующий PostgreSQL — нельзя запускать новую пустую базу!')
DATABASE_URL=os.getenv('DATABASE_URL','sqlite:///./local.db')
if DATABASE_URL.startswith('postgres://'): DATABASE_URL=DATABASE_URL.replace('postgres://','postgresql+psycopg2://',1)
elif DATABASE_URL.startswith('postgresql://'): DATABASE_URL=DATABASE_URL.replace('postgresql://','postgresql+psycopg2://',1)
engine=create_engine(DATABASE_URL,pool_pre_ping=True,connect_args={'check_same_thread':False} if DATABASE_URL.startswith('sqlite') else {})
Session=sessionmaker(bind=engine,expire_on_commit=False)
Base=declarative_base()
ECONOMY=None  # installed at import, before app lifespan runs
DAY=3600  # one economic accounting unit is one real hour
STUDY_SECONDS=12*3600  # two academic years, completed in twelve hours
OLD_YEAR_SECONDS=48*3600
YEAR_SECONDS=36*3600   # 16 -> 80 takes 96 real days
LIFESPAN_AGE=80
STIPEND_INTERVAL=24*3600  # eight Republic calendar months
STIPEND_AMOUNT=1.0
MAX_CATCHUP=30*86400
JOBS=[
 {'name':'Продавец','icon':'🛍️','start':2,'max':3.5,'food':35,'water':15,'load':'Низкая'},
 {'name':'Таксист','icon':'🚕','start':2.5,'max':4.5,'food':40,'water':20,'load':'Низкая'},
 {'name':'Уборщик','icon':'🧹','start':2,'max':3.2,'food':50,'water':20,'load':'Средняя'},
 {'name':'Курьер','icon':'📦','start':2.3,'max':4.2,'food':65,'water':25,'load':'Высокая'},
 {'name':'Повар','icon':'👨‍🍳','start':2.4,'max':5,'food':55,'water':25,'load':'Высокая'},
 {'name':'Автомеханик','icon':'🔧','start':2.8,'max':6,'food':60,'water':25,'load':'Высокая'},
 {'name':'Строитель','icon':'🏗️','start':3,'max':6.5,'food':65,'water':25,'load':'Очень высокая'},
 {'name':'Водитель грузовика','icon':'🚛','start':3.2,'max':7,'food':55,'water':30,'load':'Высокая'},
 {'name':'Парикмахер','icon':'✂️','start':2.3,'max':4.8,'food':40,'water':20,'load':'Средняя'},
 {'name':'Охранник','icon':'🛡️','start':2.2,'max':4,'food':45,'water':20,'load':'Средняя'},
 {'name':'IT-специалист','icon':'💻','start':2,'max':8,'food':35,'water':20,'load':'Низкая'}]
BUSINESSES=[
 {'name':'Клининговая компания','icon':'🧹','price':40,'profit':.5,'job':'Уборщик'},
 {'name':'Киоск','icon':'🏪','price':60,'profit':.8,'job':'Продавец'},
 {'name':'Парикмахерская','icon':'✂️','price':100,'profit':round(100/70,4),'job':'Парикмахер'},
 {'name':'Кафе','icon':'☕','price':150,'profit':round(150/65,4),'job':'Повар'},
 {'name':'Таксопарк','icon':'🚕','price':220,'profit':round(220/60,4),'job':'Таксист'},
 {'name':'Автосервис','icon':'🔧','price':350,'profit':7,'job':'Автомеханик'},
 {'name':'Строительная компания','icon':'🏗️','price':600,'profit':15,'job':'Строитель'},
 {'name':'IT-компания','icon':'💻','price':1000,'profit':round(1000/35,4),'job':'IT-специалист'}]
STUDIES=['Экономический лицей','Технический колледж','Школа искусств','IT-академия']
class Account(Base):
 __tablename__='accounts'
 id=Column(Integer,primary_key=True)
 username=Column(String(80),unique=True,nullable=False)
 password_hash=Column(String(300),nullable=False)
 role=Column(String(20),default='player',nullable=False)
 created=Column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc))
class TelegramLink(Base):
 __tablename__='telegram_links'
 telegram_id=Column(String(32),primary_key=True)
 account_id=Column(Integer,ForeignKey('accounts.id'),unique=True,nullable=False)

class Character(Base):
 __tablename__='characters'
 id=Column(Integer,primary_key=True)
 account_id=Column(Integer,ForeignKey('accounts.id'),unique=True,nullable=False)
 name=Column(String(80),nullable=False)
 state=Column(Text,nullable=False)
 last_tick=Column(Float,nullable=False)
class Token(Base):
 __tablename__='tokens'
 id=Column(Integer,primary_key=True)
 account_id=Column(Integer,ForeignKey('accounts.id'),nullable=False)
 digest=Column(String(64),unique=True,nullable=False)
 expires=Column(DateTime(timezone=True),nullable=False)
class Ledger(Base):
 __tablename__='ledger'
 id=Column(Integer,primary_key=True)
 account_id=Column(Integer,ForeignKey('accounts.id'),nullable=False)
 amount=Column(Float,nullable=False)
 reason=Column(String(160),nullable=False)
 created=Column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc))

def settle_all():
 with Session.begin() as s:
  if ECONOMY:ECONOMY.rollover(s)
  for c in s.query(Character).with_for_update(skip_locked=True).all():settle(s,c)
async def tick_loop():
 while True:
  await asyncio.sleep(3600)
  try:await asyncio.to_thread(settle_all)
  except Exception as e:print('tick error',repr(e),flush=True)
@asynccontextmanager
async def lifespan(app):
 Base.metadata.create_all(engine)
 ECONOMY.bootstrap()
 task=asyncio.create_task(tick_loop())
 try:yield
 finally:task.cancel()
app=FastAPI(title='Новая Жизнь API',version='9.0-economy',lifespan=lifespan)
origins=[x.strip() for x in os.getenv('ALLOWED_ORIGINS','https://katasadmin.github.io,http://localhost:8000,http://127.0.0.1:5500').split(',') if x.strip()]
app.add_middleware(CORSMiddleware,allow_origins=origins,allow_methods=['GET','POST'],allow_headers=['Authorization','Content-Type'])

def hash_pw(password):
 salt=secrets.token_hex(16); return salt+':'+hashlib.pbkdf2_hmac('sha256',password.encode(),bytes.fromhex(salt),220000).hex()
def verify_pw(password,stored):
 try:
  salt,digest=stored.split(':'); return hmac.compare_digest(hashlib.pbkdf2_hmac('sha256',password.encode(),bytes.fromhex(salt),220000).hex(),digest)
 except Exception:return False

def initial_state(is_first):
 return {'balance':10.0,'food':85.0,'water':85.0,'energy':90.0,'health':100.0,'age_start':16,'job':None,'employer':'state','workdays':0,'experience':{},'education':[],'study':None,'study_days':0,'businesses':[],'home':'Общежитие','family':'Не женат','alive':True,'history':['Добро пожаловать в Республику!'],'last_day':0,'clock_v2':True,'hour_fraction':0.0,'study_started_at':None,'clock_v3':True,'life_seconds_v3':0.0,'stipend_started_at':None,'stipend_initial_life':None,'stipend_payments':0}
def password_ok(p):return 8<=len(p)<=128
class Credentials(BaseModel):
 username:str=Field(min_length=3,max_length=40)
 password:str=Field(min_length=8,max_length=128)
 name:Optional[str]=Field(default=None,max_length=40)
 admin_secret:Optional[str]=None
class Action(BaseModel):
 type:str
 value:Optional[str]=None
class AdminAction(BaseModel):
 account_id:int
 field:str
 value:object
class Claim(BaseModel):
 secret:str

def get_auth(authorization:Optional[str]=Header(default=None)):
 if not authorization or not authorization.startswith('Bearer '):raise HTTPException(401,'Нужен вход в аккаунт')
 digest=hashlib.sha256(authorization[7:].encode()).hexdigest()
 with Session() as s:
  t=s.query(Token).filter(Token.digest==digest).first()
  if not t or t.expires.replace(tzinfo=timezone.utc)<datetime.now(timezone.utc):raise HTTPException(401,'Сессия истекла')
  a=s.get(Account,t.account_id)
  if not a:raise HTTPException(401,'Аккаунт не найден')
  return a.id

def add_log(s,aid,amount,reason):
 s.add(Ledger(account_id=aid,amount=round(amount,4),reason=reason))
def wage(job,shifts):
 if not job:return 0
 j=next((x for x in JOBS if x['name']==job),None)
 if not j:return 0
 level=sum(shifts>=n for n in (7,21,45))
 return round(j['start']+(j['max']-j['start'])*level/3,4)

def safe_float(value, default=0.0):
 try:
  result=float(value)
  return result if math.isfinite(result) else default
 except (TypeError, ValueError):
  return default

def prepare_state(c, now):
 """Upgrade JSON in place. No SQL schema changes; old balances and ages survive."""
 d=json.loads(c.state)
 if not d.get('clock_v2'):
  # Previous releases counted full real DAYS. In this release, last_day
  # counts real HOURS. Both representations age one year per 48 hours.
  d['last_day']=int(d.get('last_day',0))*24
  d['experience']={k:safe_float(v)*24 for k,v in d.get('experience',{}).items()}
  if d.get('study'):
   progress=min(2,max(0,safe_float(d.get('study_days',0))))
   d['study_started_at']=now-(progress/2)*STUDY_SECONDS
  d['clock_v2']=True
 d.setdefault('hour_fraction',0.0)
 d.setdefault('study_started_at',None)
 d.setdefault('education',[])
 d.setdefault('experience',{})
 d.setdefault('businesses',[])
 d.setdefault('alive',True)
 d.setdefault('age_start',16)
 d.setdefault('workdays',0)
 # Transition from the 48-hour year to a 36-hour year without instantly
 # ageing any existing account. Retain the proportion of each year lived.
 if not d.get('clock_v3'):
  elapsed=(int(d.get('last_day',0))+safe_float(d.get('hour_fraction')))*DAY
  d['life_seconds_v3']=round(max(0,elapsed)*YEAR_SECONDS/OLD_YEAR_SECONDS,6)
  d['clock_v3']=True
 d.setdefault('life_seconds_v3',0.0)
 d.setdefault('stipend_started_at',None)
 d.setdefault('stipend_initial_life',None)
 d.setdefault('stipend_payments',0)
 if d.get('stipend_started_at') is None and (d.get('study') or d.get('education')) and age_at(d)<18:
  # An existing graduate can receive a grant from this release onward.
  start=safe_float(d.get('study_started_at'),now) if d.get('study') else now
  start=min(now,max(0,start))
  d['stipend_started_at']=start
  d['stipend_initial_life']=max(0,safe_float(d['life_seconds_v3'])-max(0,now-start))
 return d

def age_at(d):
 return int(d.get('age_start',16))+int(safe_float(d.get('life_seconds_v3'))//YEAR_SECONDS)

def scholarship(s,c,d,now,credited):
 """One payment per 24h after enrollment, max until age 18, idempotent."""
 start=d.get('stipend_started_at')
 if start is None or not d.get('alive',True):return
 start=safe_float(start,now)
 if d.get('stipend_initial_life') is None:
  d['stipend_initial_life']=max(0,safe_float(d.get('life_seconds_v3'))-max(0,now-start))
 paid=max(0,int(d.get('stipend_payments',0)))
 due=max(0,int((now-start)//STIPEND_INTERVAL))
 # Never make giant catch-up payments in one request.
 due=min(due,paid+1000)
 initial=safe_float(d.get('stipend_initial_life'))
 while paid<due:
  candidate=paid+1
  # A grant is paid only if the player was still a student-age character
  # at the time it became due. Enrolment cannot be repeated for free money.
  life_at_due=initial+candidate*STIPEND_INTERVAL
  if int(d.get('age_start',16))+int(life_at_due//YEAR_SECONDS)<18:
   if ECONOMY is None or ECONOMY.locked(s).treasury>=10000:
    if ECONOMY:ECONOMY.locked(s).treasury-=10000
    d['balance']=safe_float(d.get('balance'))+STIPEND_AMOUNT
    credited['Стипендия']=credited.get('Стипендия',0)+STIPEND_AMOUNT
  paid=candidate
 d['stipend_payments']=paid

def settle(s,c):
 # Each request locks character row; all calculations and updates commit
 # atomically. Server time is authoritative, not time on the player's phone.
 now=time.time()
 d=prepare_state(c,now)
 if d.get('study'):
  if d.get('study_started_at') is None:
   d['study_started_at']=now
  age_study=max(0,now-safe_float(d.get('study_started_at'),now))
  d['study_progress']=round(min(100,age_study/STUDY_SECONDS*100),2)
  d['study_days']=round(min(2,age_study/(STUDY_SECONDS/2)),4)
  if age_study>=STUDY_SECONDS:
   if d['study'] not in d['education']:d['education'].append(d['study'])
   d['study']=None
   d['study_days']=0
   d['study_progress']=100
   d['study_started_at']=None
 else:
  d['study_progress']=100 if d.get('education') else 0
 # Do not silently skip long absences. Apply at most 30 real days per
 # transaction; further backlog will be applied on subsequent requests.
 remaining=min(MAX_CATCHUP,max(0,now-safe_float(c.last_tick,now)))
 credited={}
 # Process at most one hour per step. Old states keep their completed hours.
 while remaining>0.00001:
  frac=safe_float(d.get('hour_fraction'))
  if frac>=1-1e-10:
   d['last_day']=int(d.get('last_day',0))+1
   d['hour_fraction']=0.0
   frac=0.0
  step=min(remaining,max(0.001,(1-frac)*DAY))
  real_days=step/86400
  if d.get('alive',True):
   job_name=d.get('job')
   job=next((x for x in JOBS if x['name']==job_name),None)
   if job and ECONOMY is None:
    shifts=safe_float(d['experience'].get(job_name,0))
    pay=wage(job_name,shifts/24)*(1.12 if d.get('employer')=='private' else 1)*real_days
    d['balance']=safe_float(d.get('balance'))+pay
    d['experience'][job_name]=round(shifts+step/DAY,6)
    d['workdays']=round(safe_float(d.get('workdays'))+real_days,6)
    credited['Зарплата: '+job_name]=credited.get('Зарплата: '+job_name,0)+pay
   for b in (d['businesses'] if ECONOMY is None else []):
    idx=b.get('type',-1) if isinstance(b,dict) else -1
    if not isinstance(idx,int) or not 0<=idx<len(BUSINESSES):continue
    info=BUSINESSES[idx]
    pay=info['profit']*real_days
    d['balance']+=pay
    reason='Доход бизнеса: '+info['name']
    credited[reason]=credited.get(reason,0)+pay
   for key,use_rate,cost_rate in (('food',job['food'] if job else 35,.35),('water',job['water'] if job else 15,.15)):
    use=use_rate*real_days
    current=safe_float(d.get(key),85)
    if current<use:
     missing=100-current
     cost=missing/100*cost_rate*2
     if d['balance']>=cost:
      d['balance']-=cost
      current=100
      reason='Автопокупка ×2: '+('Еда' if key=='food' else 'Вода')
      credited[reason]=credited.get(reason,0)-cost
     else:
      d['health']=max(0,safe_float(d.get('health'),100)-.5*step/DAY)
    d[key]=round(max(0,current-use),5)
   d['energy']=round(min(100,max(0,safe_float(d.get('energy'),90)+(12-(8 if job else 0))*real_days)),5)
   if d['food']<=0 or d['water']<=0:
    d['health']=max(0,safe_float(d.get('health'),100)-.2*step/DAY)
   if d['health']<=0:
    d['alive']=False
    d['job']=None
  d['hour_fraction']=round(frac+step/DAY,10)
  if d['hour_fraction']>=1-1e-9:
   d['last_day']=int(d.get('last_day',0))+1
   d['hour_fraction']=0.0
  d['life_seconds_v3']=round(safe_float(d.get('life_seconds_v3'))+step,6)
  age=age_at(d)
  if age>=LIFESPAN_AGE:
   d['alive']=False
   d['job']=None
  remaining-=step
  c.last_tick+=step
 scholarship(s,c,d,now,credited)
 if ECONOMY:
  autopay=-(credited.get('Автопокупка ×2: Еда',0)+credited.get('Автопокупка ×2: Вода',0))
  if autopay>0:ECONOMY.credit_automatic(s,autopay)
  ECONOMY.migrate(s,c,d)
  ECONOMY.claim(s,c,d)
 # Keep small fractions in state; rounding the actual balance to two
 # decimals at each poll would prevent small wages from accumulating.
 d['balance']=round(safe_float(d.get('balance')),6)
 for reason,amount in credited.items():
  if abs(amount)>=.005:add_log(s,c.account_id,round(amount,4),reason)
 c.state=json.dumps(d,ensure_ascii=False)
 return d

def state_payload(s,a,c,d):
 history=s.query(Ledger).filter(Ledger.account_id==a.id).order_by(Ledger.id.desc()).limit(30).all()
 now=time.time()
 life_seconds=safe_float(d.get('life_seconds_v3'))+max(0,now-c.last_tick)
 age=int(d.get('age_start',16))+int(life_seconds//YEAR_SECONDS)
 if d.get('study'):
  study_seconds_left=max(0,STUDY_SECONDS-(now-safe_float(d.get('study_started_at'),now)))
 else:study_seconds_left=0
 job=next((x for x in JOBS if x['name']==d.get('job')),None)
 return {'id':str(a.id).zfill(8),'role':a.role,'name':c.name,'age':age,'data':d,
   'ledger':[{'amount':x.amount,'reason':x.reason,'date':x.created.isoformat()} for x in history],
   'server_time':now,'life_seconds':life_seconds,'year_seconds':YEAR_SECONDS,
   'study_seconds_left':study_seconds_left,'study_duration_seconds':STUDY_SECONDS,
   'stipend_next_seconds':max(0,safe_float(d.get('stipend_started_at'),now)+(int(d.get('stipend_payments',0))+1)*STIPEND_INTERVAL-now) if d.get('stipend_started_at') and age<18 and d.get('alive',True) else None,
   'stipend_amount':STIPEND_AMOUNT,'stipend_interval_months':8,
   'needs_rates':{'food':(job['food'] if job else 35)/86400,'water':(job['water'] if job else 15)/86400}}

@app.get('/api/health')
def health():
 try:
  with Session() as s:s.execute(select(Account.id).limit(1))
  return {'ok':True,'database':'connected','version':'9.0-economy'}
 except Exception:raise HTTPException(503,'База данных недоступна')
@app.get('/api/config')
def config():return {'jobs':JOBS,'businesses':BUSINESSES,'studies':STUDIES,'year_real_hours':36,'study_real_hours':12,'study_years':2,'stipend_amount':STIPEND_AMOUNT,'stipend_period_months':8,'promotions':[7,21,45],'withdrawals_enabled':False,'topups_enabled':False,'currency':'ЖН','economy_enabled':True}
@app.post('/api/register')
def register(body:Credentials):
 username=body.username.strip().lower();name=(body.name or body.username).strip()
 if username.startswith('tg_') or len(username)<3 or not username.replace('_','').isalnum() or not 2<=len(name)<=40:raise HTTPException(400,'Проверь логин и имя')
 with Session.begin() as s:
  if s.query(Account).filter(Account.username==username).first():raise HTTPException(409,'Логин занят')
  a=Account(username=username,password_hash=hash_pw(body.password),role='player');s.add(a);s.flush()
  d=initial_state(False)
  if ECONOMY:ECONOMY.add_mint(s,10)
  s.add(Character(account_id=a.id,name=name,state=json.dumps(d,ensure_ascii=False),last_tick=time.time()))
 return {'ok':True,'message':'Аккаунт создан. Войдите.'}
@app.post('/api/login')
def login(body:Credentials):
 with Session.begin() as s:
  a=s.query(Account).filter(Account.username==body.username.strip().lower()).first()
  if not a or not verify_pw(body.password,a.password_hash):raise HTTPException(401,'Неверный логин или пароль')
  token=secrets.token_urlsafe(32)
  s.add(Token(account_id=a.id,digest=hashlib.sha256(token.encode()).hexdigest(),expires=datetime.now(timezone.utc)+timedelta(days=30)))
  return {'token':token,'id':str(a.id).zfill(8)}
@app.get('/api/me')
def me(aid:int=Depends(get_auth)):
 with Session.begin() as s:
  if ECONOMY:ECONOMY.rollover(s)
  a=s.get(Account,aid);c=s.query(Character).filter(Character.account_id==aid).with_for_update().one()
  d=settle(s,c)
  if ECONOMY:ECONOMY.daily_bonus(s,c,d)
  return state_payload(s,a,c,d)
@app.post('/api/action')
def action(body:Action,aid:int=Depends(get_auth)):
 with Session.begin() as s:
  if ECONOMY:ECONOMY.rollover(s)
  a=s.get(Account,aid);c=s.query(Character).filter(Character.account_id==aid).with_for_update().one();d=settle(s,c)
  if not d['alive']:raise HTTPException(400,'Персонаж умер')
  t=body.type;v=body.value
  if t=='job':
   if v not in [j['name'] for j in JOBS]:raise HTTPException(400,'Нет такой профессии')
   if age_at(d)<18:raise HTTPException(400,'Работа доступна с 18 лет')
   if ECONOMY:
    sec=__import__('economy').JOB_TO_SECTOR.get(v)
    if sec is None:raise HTTPException(400,'Для этой профессии пока нет работодателя')
    try:ECONOMY.hire(s,c,d,sec)
    except HTTPException as e:
     if e.status_code!=409:raise
     ECONOMY.queue(s,c,d,sec)
     d['job']=None
   else:d['job']=v;d['employer']='state'
  elif t=='study':
   if v not in STUDIES:raise HTTPException(400,'Нет такой программы')
   if d.get('study'):raise HTTPException(409,'Ты уже учишься. Дождись выпуска')
   if v in d.get('education',[]):raise HTTPException(409,'Эта программа уже завершена')
   d['study']=v;d['study_days']=0;d['study_progress']=0;d['study_started_at']=time.time()
   if d.get('stipend_started_at') is None and age_at(d)<18:
    d['stipend_started_at']=d['study_started_at']
    d['stipend_initial_life']=safe_float(d.get('life_seconds_v3'))
    d['stipend_payments']=0
  elif t=='buy_business':
   try:i=int(v)
   except (ValueError,TypeError):raise HTTPException(400,'Неверный бизнес')
   if i<0 or i>=len(BUSINESSES):raise HTTPException(400,'Неверный бизнес')
   b=BUSINESSES[i]
   if d['balance']<b['price']:raise HTTPException(400,'Недостаточно средств')
   if ECONOMY:ECONOMY.buy_share(s,c,d,__import__('economy').SECTORS[i][0])
   else:
    d['balance']=round(d['balance']-b['price'],4);d['businesses'].append({'type':i});add_log(s,aid,-b['price'],'Покупка бизнеса: '+b['name'])
  elif t in ('food','water'):
   if v not in ('10','25','50','100'):raise HTTPException(400,'Выбери количество')
   qty=int(v);price=.35 if t=='food' else .15;cost=round(qty/100*price,4)
   if d['balance']<cost:raise HTTPException(400,'Недостаточно средств')
   d['balance']=round(d['balance']-cost,4);d[t]=min(100,d[t]+qty);add_log(s,aid,-cost,'Покупка: '+t)
   if ECONOMY:ECONOMY.credit_automatic(s,cost)
  else:raise HTTPException(400,'Неизвестное действие')
  c.state=json.dumps(d,ensure_ascii=False)
  return state_payload(s,a,c,d)
# Admin functionality disabled

class TelegramAuth(BaseModel):
 init_data: str = Field(min_length=1,max_length=10000)

def verify_telegram(init_data):
 bot_token=os.getenv('TELEGRAM_BOT_TOKEN','')
 if not bot_token: raise HTTPException(503,'Telegram-вход ещё не настроен: TELEGRAM_BOT_TOKEN')
 pairs=dict(parse_qsl(init_data,keep_blank_values=True))
 received=pairs.pop('hash','')
 auth_date=pairs.get('auth_date','0')
 try: age=abs(time.time()-int(auth_date))
 except ValueError: raise HTTPException(401,'Неверные данные Telegram')
 if age>3600: raise HTTPException(401,'Сессия Telegram устарела. Открой приложение снова')
 check='\n'.join(f'{k}={v}' for k,v in sorted(pairs.items()))
 secret=hmac.new(b'WebAppData',bot_token.encode(),hashlib.sha256).digest()
 expected=hmac.new(secret,check.encode(),hashlib.sha256).hexdigest()
 if not hmac.compare_digest(expected,received):raise HTTPException(401,'Не удалось проверить вход Telegram')
 try: user=json.loads(pairs['user']);uid=str(int(user['id']))
 except (KeyError,ValueError,TypeError,json.JSONDecodeError):raise HTTPException(401,'Telegram не передал ID пользователя')
 return uid,user

@app.post('/api/telegram/auth')
def telegram_auth(body:TelegramAuth):
 uid,user=verify_telegram(body.init_data)
 with Session.begin() as s:
  link=s.get(TelegramLink,uid)
  if link:
   a=s.get(Account,link.account_id)
   if not a:raise HTTPException(409,'Связанный аккаунт не найден')
  else:
   # One Telegram identity = one new game account. Linking to an existing password account requires a separate authenticated endpoint.
   username='tg_'+uid
   a=s.query(Account).filter(Account.username==username).first()
   if a is None:
    a=Account(username=username,password_hash=hash_pw(secrets.token_urlsafe(32)),role='player');s.add(a);s.flush()
    if ECONOMY:ECONOMY.add_mint(s,10)
    name=str(user.get('first_name') or 'Игрок')[:40]
    s.add(Character(account_id=a.id,name=name,state=json.dumps(initial_state(False),ensure_ascii=False),last_tick=time.time()))
   s.add(TelegramLink(telegram_id=uid,account_id=a.id))
  token=secrets.token_urlsafe(32)
  s.add(Token(account_id=a.id,digest=hashlib.sha256(token.encode()).hexdigest(),expires=datetime.now(timezone.utc)+timedelta(days=30)))
  return {'token':token,'id':str(a.id).zfill(8)}


# v10: account recovery methods, read from PostgreSQL; existing data unchanged.
class SecurityPassword(BaseModel):
 username:str=Field(min_length=3,max_length=40)
 password:str=Field(min_length=8,max_length=128)

@app.get('/api/security')
def security_status(aid:int=Depends(get_auth)):
 with Session() as db:
  a=db.get(Account,aid)
  if not a:raise HTTPException(404,'Аккаунт не найден')
  linked=db.query(TelegramLink).filter(TelegramLink.account_id==aid).first() is not None
  return {'telegram':linked,'password':not a.username.startswith('tg_')}

@app.post('/api/security/password')
def security_password(body:SecurityPassword,aid:int=Depends(get_auth)):
 with Session.begin() as db:
  a=db.get(Account,aid)
  if not a:raise HTTPException(404,'Аккаунт не найден')
  if not a.username.startswith('tg_'):raise HTTPException(409,'Логин и пароль уже настроены')
  username=body.username.strip().lower()
  if username.startswith('tg_') or not username.replace('_','').isalnum():raise HTTPException(400,'Недопустимый логин')
  if db.query(Account).filter(Account.username==username).first():raise HTTPException(409,'Этот логин занят')
  a.username=username
  a.password_hash=hash_pw(body.password)
  return {'ok':True}

@app.post('/api/security/telegram')
def security_telegram(body:TelegramAuth,aid:int=Depends(get_auth)):
 uid,_=verify_telegram(body.init_data)
 with Session.begin() as db:
  existing=db.get(TelegramLink,uid)
  if existing:
   if existing.account_id==aid:return {'ok':True}
   raise HTTPException(409,'Этот Telegram уже привязан к другому игровому аккаунту')
  if db.query(TelegramLink).filter(TelegramLink.account_id==aid).first():raise HTTPException(409,'К аккаунту уже привязан Telegram')
  db.add(TelegramLink(telegram_id=uid,account_id=aid))
  return {'ok':True}


# Economy is registered after authentication routes, before FastAPI lifespan runs.
from economy import Economy
ECONOMY=Economy(Base,Session,Account,Character,Ledger,add_log,age_at,safe_float)
ECONOMY.register_routes(app,get_auth,settle,state_payload)
