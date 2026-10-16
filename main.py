"""NOVA LIFE: server-side prototype. All amounts are virtual coins."""
import os
import secrets
from datetime import datetime, timezone, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional
from fastapi import FastAPI, HTTPException, Header, Depends
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, String, Integer, DateTime, ForeignKey, select, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, Session, sessionmaker

DATABASE_URL = os.getenv('DATABASE_URL', 'sqlite:///./nova_life.db')
if DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql+psycopg://', 1)
elif DATABASE_URL.startswith('postgresql://'):
    DATABASE_URL = DATABASE_URL.replace('postgresql://', 'postgresql+psycopg://', 1)
engine = create_engine(DATABASE_URL, pool_pre_ping=True, connect_args={'check_same_thread': False} if DATABASE_URL.startswith('sqlite') else {})
SessionLocal = sessionmaker(engine, expire_on_commit=False)
class Base(DeclarativeBase): pass

def utcnow(): return datetime.now(timezone.utc).replace(tzinfo=None)
# Monetary values are stored as integer kopecks to avoid float drift.
def coins(v): return int((Decimal(str(v))*100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
def show(v): return round(v/100, 2)

BUSINESSES = {
 'cleaning': ('Клининговая компания',50,.80), 'delivery': ('Курьерская служба',65,1),
 'barber': ('Парикмахерская',80,1.20), 'taxi': ('Таксопарк',100,1.50),
 'shop': ('Магазин',130,2), 'cafe': ('Кафе',160,2.50),
 'security': ('Охранная компания',200,3), 'garage': ('Автосервис',250,4),
 'freight': ('Грузоперевозки',350,5.50), 'construction': ('Строительная компания',500,8),
 'it': ('IT-компания',800,12),
}
JOBS = {
 'seller':('Продавец',[2,2.4,2.8,3.2,3.5]), 'taxi':('Таксист',[2.2,2.5,2.9,3.3,3.7]),
 'courier':('Курьер',[2,2.3,2.7,3.1,3.6]), 'cook':('Повар',[2,2.4,2.9,3.5,4]),
 'cleaner':('Уборщик',[2,2.2,2.5,2.9,3.2]), 'mechanic':('Механик',[1.8,2.2,2.8,3.8,4.5]),
 'builder':('Строитель',[2.1,2.5,3,3.8,4.4]), 'trucker':('Дальнобойщик',[2,2.4,2.9,3.5,4]),
 'hairdresser':('Парикмахер',[1.8,2.2,2.7,3.3,4]), 'guard':('Охранник',[2,2.3,2.7,3.2,3.6]),
 'developer':('IT-специалист',[1.5,1.8,2.4,3.6,5.5]),
}
REF_RATES = [Decimal('.05'),Decimal('.03'),Decimal('.015'),Decimal('.0075'),Decimal('.0025'),Decimal('.001')]

class Player(Base):
 __tablename__='players'
 id: Mapped[int] = mapped_column(primary_key=True)
 name: Mapped[str] = mapped_column(String(40), unique=True)
 token: Mapped[str] = mapped_column(String(128), unique=True)
 balance: Mapped[int] = mapped_column(Integer, default=1000)
 born_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
 last_settled: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
 last_task: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
 last_bonus: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
 last_manual_needs: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
 hunger_days: Mapped[int] = mapped_column(Integer, default=0)
 alive: Mapped[bool] = mapped_column(default=True)
 job: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
 job_since: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
 invited_by: Mapped[Optional[int]] = mapped_column(ForeignKey('players.id'), nullable=True)

class OwnedBusiness(Base):
 __tablename__='owned_businesses'
 id: Mapped[int] = mapped_column(primary_key=True)
 owner_id: Mapped[int] = mapped_column(ForeignKey('players.id'), index=True)
 kind: Mapped[str] = mapped_column(String(30))
 acquired_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

class Ledger(Base):
 __tablename__='ledger'
 id: Mapped[int] = mapped_column(primary_key=True)
 player_id: Mapped[int] = mapped_column(ForeignKey('players.id'), index=True)
 amount: Mapped[int] = mapped_column(Integer)
 reason: Mapped[str] = mapped_column(String(100))
 created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

Base.metadata.create_all(engine)
app=FastAPI(title='НОВАЯ ЖИЗНЬ API',version='0.1.0')

def db_session():
 with SessionLocal() as db: yield db

def current_player(authorization: Optional[str]=Header(None), db:Session=Depends(db_session)):
 if not authorization or not authorization.startswith('Bearer '): raise HTTPException(401,'Требуется Bearer токен')
 p=db.scalar(select(Player).where(Player.token==authorization[7:]))
 if not p: raise HTTPException(401,'Неверный токен')
 return p

def record(db,p,amount,reason):
 p.balance += amount
 db.add(Ledger(player_id=p.id,amount=amount,reason=reason))

def referral(db,p,amount):
 """Referral rewards on earned salary/business profit, not on bonuses or referrals."""
 ancestor_id=p.invited_by
 for rate in REF_RATES:
  if not ancestor_id: break
  parent=db.get(Player,ancestor_id)
  if not parent or parent.id==p.id: break
  if parent.alive:
   reward=int((Decimal(amount)*rate).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
   if reward: record(db,parent,reward,f'Реферальный доход от игрока {p.id}')
  ancestor_id=parent.invited_by

def age_years(p,now):
 days=(now-p.born_at).total_seconds()/86400
 return 16 + min(2,int(days)) if days<2 else 18+int((days-2)//7)

def salary(p,at):
 if not p.job or not p.job_since: return 0
 days=max(0,int((at-p.job_since).total_seconds()//86400))
 level=4 if days>=60 else 3 if days>=30 else 2 if days>=14 else 1 if days>=7 else 0
 return coins(JOBS[p.job][1][level])

def settle(db,p,now=None):
 """Settle complete 24-hour intervals, limited to 365 per request for predictable load."""
 now=now or utcnow()
 elapsed=int((now-p.last_settled).total_seconds()//86400)
 for _ in range(min(max(elapsed,0),365)):
  day=p.last_settled+timedelta(days=1)
  if not p.alive: break
  if age_years(p,day)>=85:
   p.alive=False; break
  # Passive income and salary continue offline.
  earned=salary(p,day)
  for b in db.scalars(select(OwnedBusiness).where(OwnedBusiness.owner_id==p.id)).all(): earned+=coins(BUSINESSES[b.kind][2])
  if earned: record(db,p,earned,'Ежедневная зарплата и доход предприятий'); referral(db,p,earned)
  # Manual purchase covers one daily needs cycle; otherwise auto-purchase costs twice as much.
  manual=p.last_manual_needs and day-timedelta(days=1)<=p.last_manual_needs<day
  cost=0 if manual else coins(2.60)
  if p.balance>=cost:
   if cost: record(db,p,-cost,'Автоматические потребности')
   p.hunger_days=0
  else:
   p.hunger_days+=1
   if p.hunger_days>=7: p.alive=False
  p.last_settled=day
 db.commit()
 return p

class Registration(BaseModel):
 name: str=Field(min_length=3,max_length=40,pattern=r'^[a-zA-Zа-яА-ЯёЁіІїЇєЄґҐ0-9_]+$')
 referral_code: Optional[int]=None

@app.get('/health')
def health(): return {'status':'ok'}

@app.get('/catalog')
def catalog():
 return {'businesses':{k:{'name':v[0],'price':v[1],'income_per_day':v[2]} for k,v in BUSINESSES.items()},'jobs':{k:{'name':v[0],'salary_levels':v[1]} for k,v in JOBS.items()},'referral_percent':[5,3,1.5,.75,.25,.1]}

@app.post('/register')
def register(payload:Registration,db:Session=Depends(db_session)):
 if db.scalar(select(Player).where(Player.name==payload.name)): raise HTTPException(409,'Имя занято')
 inviter=db.get(Player,payload.referral_code) if payload.referral_code else None
 if payload.referral_code and not inviter: raise HTTPException(404,'Пригласивший не найден')
 p=Player(name=payload.name,token=secrets.token_urlsafe(36),invited_by=inviter.id if inviter else None)
 db.add(p);db.commit();db.refresh(p)
 return {'id':p.id,'token':p.token,'balance':show(p.balance),'referral_code':p.id}

@app.get('/me')
def me(p:Player=Depends(current_player),db:Session=Depends(db_session)):
 settle(db,p)
 return {'id':p.id,'name':p.name,'balance':show(p.balance),'age':age_years(p,utcnow()),'alive':p.alive,'job':p.job,'salary_per_day':show(salary(p,utcnow())),'hunger_days':p.hunger_days,'task_available':not p.last_task or (utcnow()-p.last_task)>=timedelta(hours=24),'referral_code':p.id,'businesses':[{'kind':b.kind,'id':b.id} for b in db.scalars(select(OwnedBusiness).where(OwnedBusiness.owner_id==p.id))]}

@app.post('/job/{kind}')
def get_job(kind:str,p:Player=Depends(current_player),db:Session=Depends(db_session)):
 settle(db,p)
 if not p.alive: raise HTTPException(400,'Персонаж умер')
 if age_years(p,utcnow())<18: raise HTTPException(400,'Работа доступна с 18 лет')
 if kind not in JOBS: raise HTTPException(404,'Нет профессии')
 if p.job!=kind: p.job=kind;p.job_since=utcnow()
 db.commit();return {'job':kind,'salary_per_day':show(salary(p,utcnow()))}

@app.post('/business/{kind}/buy')
def buy_business(kind:str,p:Player=Depends(current_player),db:Session=Depends(db_session)):
 settle(db,p)
 if not p.alive: raise HTTPException(400,'Персонаж умер')
 if age_years(p,utcnow())<18: raise HTTPException(400,'Бизнес доступен с 18 лет')
 if kind not in BUSINESSES: raise HTTPException(404,'Нет бизнеса')
 price=coins(BUSINESSES[kind][1])
 if p.balance<price: raise HTTPException(400,'Недостаточно средств')
 record(db,p,-price,'Покупка бизнеса '+kind)
 db.add(OwnedBusiness(owner_id=p.id,kind=kind));db.commit()
 return {'business':kind,'price':show(price),'balance':show(p.balance)}

@app.post('/needs/buy')
def buy_needs(p:Player=Depends(current_player),db:Session=Depends(db_session)):
 settle(db,p)
 if not p.alive: raise HTTPException(400,'Персонаж умер')
 now=utcnow()
 if p.last_manual_needs and (now-p.last_manual_needs).total_seconds()<86400: raise HTTPException(400,'Потребности уже оплачены на сутки')
 cost=coins(1.30)
 if p.balance<cost: raise HTTPException(400,'Недостаточно средств')
 record(db,p,-cost,'Питание, вода, жильё, транспорт')
 p.last_manual_needs=now; p.hunger_days=0
 db.commit();return {'balance':show(p.balance),'covered_until':(now+timedelta(days=1)).isoformat()}

@app.post('/bonus/daily')
def bonus(p:Player=Depends(current_player),db:Session=Depends(db_session)):
 settle(db,p)
 if not p.alive: raise HTTPException(400,'Персонаж умер')
 now=utcnow()
 if p.last_bonus and now-p.last_bonus<timedelta(hours=24): raise HTTPException(400,'Бонус уже получен')
 record(db,p,coins(1),'Бонус за вход');p.last_bonus=now;db.commit()
 return {'bonus':1,'balance':show(p.balance)}

@app.get('/ledger')
def ledger(p:Player=Depends(current_player),db:Session=Depends(db_session)):
 settle(db,p)
 rows=db.scalars(select(Ledger).where(Ledger.player_id==p.id).order_by(Ledger.id.desc()).limit(50)).all()
 return [{'amount':show(x.amount),'reason':x.reason,'at':x.created_at.isoformat()} for x in rows]

TASKS = {
 'seller': ('Проверить остатки товара', .20), 'taxi': ('Выполнить выгодный заказ', .20),
 'courier': ('Доставить посылку', .20), 'cook': ('Приготовить заказ', .20),
 'cleaner': ('Убрать помещение', .20), 'mechanic': ('Провести диагностику', .20),
 'builder': ('Закончить этап стройки', .20), 'trucker': ('Спланировать маршрут', .20),
 'hairdresser': ('Обслужить клиента', .20), 'guard': ('Проверить объект', .20),
 'developer': ('Исправить ошибку', .20),
}

@app.get('/tasks')
def tasks(p:Player=Depends(current_player),db:Session=Depends(db_session)):
 settle(db,p)
 if not p.job: return {'task':None,'available':False}
 title,reward=TASKS[p.job]
 return {'task':title,'reward':reward,'available':p.alive and (not p.last_task or utcnow()-p.last_task>=timedelta(hours=24))}

@app.post('/tasks/complete')
def complete_task(p:Player=Depends(current_player),db:Session=Depends(db_session)):
 settle(db,p)
 if not p.alive or not p.job: raise HTTPException(400,'Нет активной работы')
 now=utcnow()
 if p.last_task and now-p.last_task<timedelta(hours=24): raise HTTPException(400,'Задание уже выполнено')
 title,reward=TASKS[p.job]
 amount=coins(reward)
 record(db,p,amount,'Рабочее задание: '+title)
 referral(db,p,amount)
 p.last_task=now
 db.commit()
 return {'earned':reward,'balance':show(p.balance)}
