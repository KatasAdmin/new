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

DATABASE_URL=os.getenv('DATABASE_URL','sqlite:///./local.db')
if DATABASE_URL.startswith('postgres://'): DATABASE_URL=DATABASE_URL.replace('postgres://','postgresql+psycopg2://',1)
elif DATABASE_URL.startswith('postgresql://'): DATABASE_URL=DATABASE_URL.replace('postgresql://','postgresql+psycopg2://',1)
engine=create_engine(DATABASE_URL,pool_pre_ping=True,connect_args={'check_same_thread':False} if DATABASE_URL.startswith('sqlite') else {})
Session=sessionmaker(bind=engine,expire_on_commit=False)
Base=declarative_base()
DAY=86400
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
  for c in s.query(Character).with_for_update(skip_locked=True).all():settle(s,c)
async def tick_loop():
 while True:
  await asyncio.sleep(3600)
  try:await asyncio.to_thread(settle_all)
  except Exception as e:print('tick error',repr(e),flush=True)
@asynccontextmanager
async def lifespan(app):
 Base.metadata.create_all(engine)
 task=asyncio.create_task(tick_loop())
 try:yield
 finally:task.cancel()
app=FastAPI(title='Новая Жизнь API',version='5.0',lifespan=lifespan)
origins=[x.strip() for x in os.getenv('ALLOWED_ORIGINS','https://katasadmin.github.io,http://localhost:8000,http://127.0.0.1:5500').split(',') if x.strip()]
app.add_middleware(CORSMiddleware,allow_origins=origins,allow_methods=['GET','POST'],allow_headers=['Authorization','Content-Type'])

def hash_pw(password):
 salt=secrets.token_hex(16); return salt+':'+hashlib.pbkdf2_hmac('sha256',password.encode(),bytes.fromhex(salt),220000).hex()
def verify_pw(password,stored):
 try:
  salt,digest=stored.split(':'); return hmac.compare_digest(hashlib.pbkdf2_hmac('sha256',password.encode(),bytes.fromhex(salt),220000).hex(),digest)
 except Exception:return False

def initial_state(is_first):
 return {'balance':10.0,'food':85.0,'water':85.0,'energy':90.0,'health':100.0,'age_start':16,'job':None,'employer':'state','workdays':0,'experience':{},'education':[],'study':None,'study_days':0,'businesses':[],'home':'Общежитие','family':'Не женат','alive':True,'history':['Добро пожаловать в Республику!'],'last_day':0}
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

def settle(s,c):
 # Settlement is locked per character on PostgreSQL; completed ticks are saved in one transaction.
 d=json.loads(c.state);now=time.time(); elapsed=max(0,min(int((now-c.last_tick)//DAY),3650))
 if not elapsed:return d
 for _ in range(elapsed):
  if d['alive']:
   if d['job']:
    shifts=d['experience'].get(d['job'],0)
    pay=wage(d['job'],shifts)*(1.12 if d['employer']=='private' else 1)
    d['balance']=round(d['balance']+pay,4)
    d['experience'][d['job']]=shifts+1;d['workdays']+=1
    add_log(s,c.account_id,pay,'Зарплата: '+d['job'])
   elif d['age_start']<18 and d['study']:
    d['balance']=round(d['balance']+.5,4);add_log(s,c.account_id,.5,'Стипендия')
   for b in d['businesses']:
    info=BUSINESSES[b['type']];d['balance']=round(d['balance']+info['profit'],4)
    add_log(s,c.account_id,info['profit'],'Доход бизнеса: '+info['name'])
   job=next((x for x in JOBS if x['name']==d['job']),None)
   food_use=(job['food'] if job else 35);water_use=(job['water'] if job else 15)
   for key,need,price in [('food',food_use,.35),('water',water_use,.15)]:
    if d[key]<need:
     missing=100-d[key];cost=round(missing/100*price*2,4)
     if d['balance']>=cost:
      d['balance']=round(d['balance']-cost,4);d[key]+=missing;add_log(s,c.account_id,-cost,'Автопокупка ×2: '+key)
     else:d['health']=max(0,d['health']-12)
    d[key]=max(0,round(d[key]-need,2))
   d['energy']=min(100,max(0,d['energy']-(8 if job else 0)+12))
   if d['food']<=0 or d['water']<=0:d['health']=max(0,d['health']-5)
   if d['study']:
    d['study_days']+=1
    if d['study_days']>=2:
     if d['study'] not in d['education']:d['education'].append(d['study'])
     d['study']=None;d['study_days']=0
   if d['health']<=0:d['alive']=False;d['job']=None
  d['last_day']=d.get('last_day',0)+1
  max_age=65+(c.account_id*37)%31
  if d['age_start']+d['last_day']//2>=max_age:
   d['alive']=False;d['job']=None
  c.last_tick+=DAY
 d['balance']=round(d['balance'],2)
 c.state=json.dumps(d,ensure_ascii=False)
 return d

def state_payload(s,a,c,d):
 history=s.query(Ledger).filter(Ledger.account_id==a.id).order_by(Ledger.id.desc()).limit(30).all()
 age=d['age_start']+d.get('last_day',0)//2
 return {'id':str(a.id).zfill(8),'role':a.role,'name':c.name,'age':age,'data':d,'ledger':[{'amount':x.amount,'reason':x.reason,'date':x.created.isoformat()} for x in history],'server_time':time.time()}

@app.get('/api/health')
def health():
 try:
  with Session() as s:s.execute(select(Account.id).limit(1))
  return {'ok':True,'database':'connected','version':'5.0'}
 except Exception:raise HTTPException(503,'База данных недоступна')
@app.get('/api/config')
def config():return {'jobs':JOBS,'businesses':BUSINESSES,'studies':STUDIES,'year_real_days':2,'promotions':[7,21,45],'withdrawals_enabled':False,'topups_enabled':False}
@app.post('/api/register')
def register(body:Credentials):
 username=body.username.strip().lower();name=(body.name or body.username).strip()
 if len(username)<3 or not username.replace('_','').isalnum() or not 2<=len(name)<=40:raise HTTPException(400,'Проверь логин и имя')
 with Session.begin() as s:
  if s.query(Account).filter(Account.username==username).first():raise HTTPException(409,'Логин занят')
  a=Account(username=username,password_hash=hash_pw(body.password),role='player');s.add(a);s.flush()
  d=initial_state(False)
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
  a=s.get(Account,aid);c=s.query(Character).filter(Character.account_id==aid).with_for_update().one()
  d=settle(s,c);return state_payload(s,a,c,d)
@app.post('/api/action')
def action(body:Action,aid:int=Depends(get_auth)):
 with Session.begin() as s:
  a=s.get(Account,aid);c=s.query(Character).filter(Character.account_id==aid).with_for_update().one();d=settle(s,c)
  if not d['alive']:raise HTTPException(400,'Персонаж умер')
  t=body.type;v=body.value
  if t=='job':
   if v not in [j['name'] for j in JOBS]:raise HTTPException(400,'Нет такой профессии')
   if d['age_start']+d['last_day']//2<18:raise HTTPException(400,'Работа доступна с 18 лет')
   d['job']=v;d['employer']='state'
  elif t=='study':
   if v not in STUDIES:raise HTTPException(400,'Нет такой программы')
   d['study']=v;d['study_days']=0
  elif t=='buy_business':
   try:i=int(v)
   except (ValueError,TypeError):raise HTTPException(400,'Неверный бизнес')
   if i<0 or i>=len(BUSINESSES):raise HTTPException(400,'Неверный бизнес')
   b=BUSINESSES[i]
   if d['balance']<b['price']:raise HTTPException(400,'Недостаточно средств')
   d['balance']=round(d['balance']-b['price'],4);d['businesses'].append({'type':i});add_log(s,aid,-b['price'],'Покупка бизнеса: '+b['name'])
  elif t in ('food','water'):
   if v not in ('10','25','50','100'):raise HTTPException(400,'Выбери количество')
   qty=int(v);price=.35 if t=='food' else .15;cost=round(qty/100*price,4)
   if d['balance']<cost:raise HTTPException(400,'Недостаточно средств')
   d['balance']=round(d['balance']-cost,4);d[t]=min(100,d[t]+qty);add_log(s,aid,-cost,'Покупка: '+t)
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
    name=str(user.get('first_name') or 'Игрок')[:40]
    s.add(Character(account_id=a.id,name=name,state=json.dumps(initial_state(False),ensure_ascii=False),last_tick=time.time()))
   s.add(TelegramLink(telegram_id=uid,account_id=a.id))
  token=secrets.token_urlsafe(32)
  s.add(Token(account_id=a.id,digest=hashlib.sha256(token.encode()).hexdigest(),expires=datetime.now(timezone.utc)+timedelta(days=30)))
  return {'token':token,'id':str(a.id).zfill(8)}
