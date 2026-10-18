"""Closed-ledger virtual economy for NOVAYA ZHIZN.
All monetary movements are denominated in *virtual* ZHN, never real UAH.
Existing accounts and Character.state are preserved; new tables are additive.
"""
import time, json, math
from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Float, UniqueConstraint
from sqlalchemy.orm import declarative_base
from fastapi import HTTPException, Depends
from pydantic import BaseModel, Field

# Sector profit is linked to purchases; NO passive interest/profit without revenue.
SECTORS = [
 ('clean','🧹 Клининговая компания',40,'Уборщик', 'Уборка жилья'),
 ('retail','🏪 Магазины и киоски',60,'Продавец','Продажа товаров'),
 ('beauty','✂️ Парикмахерские',100,'Парикмахер','Стрижка'),
 ('coffee','☕ Кофейни и кафе',150,'Повар','Кофе и готовая еда'),
 ('taxi','🚕 Таксопарки',220,'Таксист','Поездки'),
 ('repair','🔧 Автосервисы',350,'Автомеханик','Ремонт автомобиля'),
 ('construction','🏗️ Строительные компании',600,'Строитель','Строительство'),
 ('it','💻 IT-компании',1000,'IT-специалист','IT-услуги'),
 ('energy','⚡ ТЭЦ и электричество',400,'Электрик','Электричество'),
 ('farm','🌾 Фермы и производство',140,'Фермер','Продукты'),
 ('logistics','🚚 Логистика',180,'Курьер','Доставка'),
 ('water','💧 Водоканал',220,'Оператор водоканала','Вода'),
]
SECTOR_MAP={r[0]:r for r in SECTORS}
JOB_TO_SECTOR={r[3]:r[0] for r in SECTORS}
# Price and effects per full 100-pt unit or individual item.
ITEMS = {
 'food': {'name':'🥪 Базовая еда +25','price':.18,'food':25,'water':0,'energy':0,'sector':'retail','suppliers':{'farm':.25,'energy':.06,'logistics':.06}},
 'water':{'name':'💧 Вода +25','price':.08,'food':0,'water':25,'energy':0,'sector':'water','suppliers':{'energy':.10,'logistics':.04}},
 'coffee':{'name':'☕ Кофе +20 энергии','price':.80,'food':0,'water':0,'energy':20,'sector':'coffee','suppliers':{'farm':.22,'energy':.10,'logistics':.06}},
 'meal':{'name':'🍽️ Обед +60 еды','price':1.50,'food':60,'water':0,'energy':10,'sector':'coffee','suppliers':{'farm':.30,'energy':.10,'logistics':.06}},
 'taxi':{'name':'🚕 Поездка','price':1.00,'food':0,'water':0,'energy':5,'sector':'taxi','suppliers':{'energy':.15}},
 'haircut':{'name':'✂️ Стрижка','price':2.00,'food':0,'water':0,'energy':0,'sector':'beauty','suppliers':{'energy':.10}},
 'clean':{'name':'🧹 Уборка','price':1.20,'food':0,'water':0,'energy':0,'sector':'clean','suppliers':{'energy':.08}},
 'repair':{'name':'🔧 Ремонт','price':3.00,'food':0,'water':0,'energy':0,'sector':'repair','suppliers':{'energy':.10,'logistics':.15}},
 'it':{'name':'💻 IT-сервис','price':3.00,'food':0,'water':0,'energy':0,'sector':'it','suppliers':{'energy':.15}},
 'construction':{'name':'🏗️ Строительные работы','price':4.00,'food':0,'water':0,'energy':0,'sector':'construction','suppliers':{'energy':.15,'logistics':.10}},
}
TAX_RATE=.08
# Within each received sector payment: 40% payroll, 20% business cash, 40% net profit.
WAGE_RATE=.40
RETAIN_RATE=.20
OWNER_RATE=.40
TARGET_DAILY_WAGE=1.0  # prospective not guaranteed for all days; wages are paid from the fund
BASE_CAPACITY=40
CAPACITY_PER_HIRE=30


def units(v): return int(round(float(v)*10000))
def amount(v):return round(int(v)/10000,4)
def today():return int(time.time()//86400)

class Economy:
 def __init__(self,Base,Session,Account,Character,Ledger,add_log,age_at,safe_float):
  self.Session=Session;self.Account=Account;self.Character=Character
  self.Ledger=Ledger;self.add_log=add_log;self.age_at=age_at;self.safe_float=safe_float
  class Control(Base):
   __tablename__='econ_control'
   id=Column(Integer,primary_key=True)
   treasury=Column(Integer,nullable=False,default=0)
   bank_capital=Column(Integer,nullable=False,default=0)
   bank_earnings=Column(Integer,nullable=False,default=0)
   minted=Column(Integer,nullable=False,default=0)
   burned=Column(Integer,nullable=False,default=0)
   last_payroll=Column(Integer,nullable=False,default=0)
   last_interest=Column(Integer,nullable=False,default=0)
  class Sector(Base):
   __tablename__='econ_sectors'
   key=Column(String(30),primary_key=True)
   wage_pool=Column(Integer,nullable=False,default=0)
   retained=Column(Integer,nullable=False,default=0)
   total_revenue=Column(Integer,nullable=False,default=0)
   total_profit=Column(Integer,nullable=False,default=0)
   total_payroll=Column(Integer,nullable=False,default=0)
   day=Column(Integer,nullable=False,default=0)
   day_units=Column(Integer,nullable=False,default=0)
  class Holding(Base):
   __tablename__='econ_holdings'
   id=Column(Integer,primary_key=True)
   account_id=Column(Integer,nullable=False,index=True)
   sector=Column(String(30),nullable=False,index=True)
   shares=Column(Integer,nullable=False,default=1)
   __table_args__=(UniqueConstraint('account_id','sector',name='uq_econ_holding'),)
  class Worker(Base):
   __tablename__='econ_workers'
   account_id=Column(Integer,primary_key=True)
   sector=Column(String(30),nullable=False,index=True)
   joined_day=Column(Integer,nullable=False)
   joined_at=Column(Float,nullable=False,default=time.time)
  class Queue(Base):
   __tablename__='econ_queue'
   id=Column(Integer,primary_key=True)
   account_id=Column(Integer,nullable=False,index=True)
   sector=Column(String(30),nullable=False,index=True)
   applied_day=Column(Integer,nullable=False)
   __table_args__=(UniqueConstraint('account_id','sector',name='uq_econ_queue'),)
  class Hire(Base):
   __tablename__='econ_hires'
   id=Column(Integer,primary_key=True)
   worker_id=Column(Integer,nullable=False,index=True)
   initiator_id=Column(Integer,nullable=True)
   sector=Column(String(30),nullable=False,index=True)
   slot_start=Column(Integer,nullable=False)
   slot_end=Column(Integer,nullable=False)
   active=Column(Integer,nullable=False,default=1)
  class Pending(Base):
   __tablename__='econ_pending'
   account_id=Column(Integer,primary_key=True)
   value=Column(Integer,nullable=False,default=0)
  class Deposit(Base):
   __tablename__='econ_deposits'
   account_id=Column(Integer,primary_key=True)
   principal=Column(Integer,nullable=False,default=0)
  class Loan(Base):
   __tablename__='econ_loans'
   account_id=Column(Integer,primary_key=True)
   principal=Column(Integer,nullable=False,default=0)
   interest_due=Column(Integer,nullable=False,default=0)
  self.Control=Control;self.Sector=Sector;self.Holding=Holding;self.Worker=Worker;self.Queue=Queue;self.Hire=Hire;self.Pending=Pending;self.Deposit=Deposit;self.Loan=Loan

 def locked(self,s):
  c=s.query(self.Control).filter_by(id=1).with_for_update().one_or_none()
  if c is None:raise RuntimeError('Economy schema not bootstrapped')
  return c

 def bootstrap(self):
  with self.Session.begin() as s:
   ctrl=s.get(self.Control,1)
   if ctrl is None:
    # One-time, explicitly declared VIRTUAL liquidity. Never real UAH backing.
    ctrl=self.Control(id=1,treasury=units(1600),bank_capital=units(500),bank_earnings=units(100),minted=units(2500),burned=0,last_payroll=today()-1,last_interest=today()-1)
    s.add(ctrl)
    # Existing game balances are legacy issuance (not real-world backing).
    existing=0
    for player in s.query(self.Character).all():
     try:existing+=units(json.loads(player.state).get('balance',0))
     except (TypeError,ValueError,json.JSONDecodeError):pass
    ctrl.minted+=existing
   for key, *_ in SECTORS:
    if s.get(self.Sector,key) is None:
     s.add(self.Sector(key=key,wage_pool=units(25),retained=0,day=today(),day_units=0))
   # Starting 12×25 sector pots plus 1600+500+100=2500, exactly.
   # Count existing wallets as legacy virtual supply only once.
   # Existing player balances are preserved, not duplicated, and do not claim real backing.
   if ctrl.last_payroll < 0:ctrl.last_payroll=today()-1

 def add_mint(self,s,zhn):
  self.locked(s).minted+=units(zhn)

 def get_char(self,s,aid):
  ch=s.query(self.Character).filter_by(account_id=aid).with_for_update().one_or_none()
  if ch is None:raise HTTPException(404,'Персонаж не найден')
  return ch

 def save(self,c,d):c.state=json.dumps(d,ensure_ascii=False)

 def migrate(self,s,c,d):
  if d.get('economy_v1'):return
  # Existing owners keep their purchased shares; preserve original business list for UI/backups.
  owned={}
  for b in d.get('businesses',[]):
   if isinstance(b,dict) and isinstance(b.get('type'),int) and 0<=b['type']<8:
    key=SECTORS[b['type']][0];owned[key]=owned.get(key,0)+1
  for key,qty in owned.items():
   h=s.query(self.Holding).filter_by(account_id=c.account_id,sector=key).one_or_none()
   if h:h.shares+=qty
   else:s.add(self.Holding(account_id=c.account_id,sector=key,shares=qty))
  # Earlier automatic state wages are not mirrored into the new system: no double payment.
  d['economy_v1']=True
  # Preserve previously hired citizens by transferring them to a funded job
  # where possible. Otherwise keep their profession in a dated waiting list.
  old_job=d.get('job')
  dest=JOB_TO_SECTOR.get(old_job)
  if dest and not s.get(self.Worker,c.account_id) and self.age_at(d)>=18 and d.get('alive',True):
   try:self.hire(s,c,d,dest)
   except HTTPException as ex:
    if ex.status_code!=409:raise
    self.queue(s,c,d,dest)
    d['job']=None
  self.save(c,d)

 def claim(self,s,c,d):
  p=s.get(self.Pending,c.account_id)
  if p and p.value>0:
   d['balance']=amount(units(d.get('balance',0))+p.value)
   self.add_log(s,c.account_id,amount(p.value),'Дивиденды и зарплаты экономики')
   p.value=0
   self.save(c,d)

 def pending_credit(self,s,aid,v):
  if v<=0:return
  p=s.get(self.Pending,aid)
  if p is None:p=self.Pending(account_id=aid,value=0);s.add(p)
  p.value+=int(v)

 def owner_pay(self,s,sector,profit,initiator=None):
  if profit<=0:return
  ctrl=self.locked(s)
  holders=s.query(self.Holding).filter_by(sector=sector).order_by(self.Holding.account_id).all()
  total=sum(h.shares for h in holders)
  if not total:
   ctrl.treasury+=profit;return
  incentive=profit//5 if initiator and any(h.account_id==initiator for h in holders) else 0
  if incentive:self.pending_credit(s,initiator,incentive)
  remaining=profit-incentive
  allocated=0
  for h in holders:
   share=remaining*h.shares//total
   self.pending_credit(s,h.account_id,share);allocated+=share
  ctrl.treasury+=remaining-allocated

 def sector_receive(self,s,key,n,primary=False):
  if n<=0:return
  sec=s.get(self.Sector,key)
  if sec is None:raise RuntimeError('sector missing '+key)
  sec.total_revenue+=n
  wage=n*40//100; retain=n*20//100; profit=n-wage-retain
  sec.wage_pool+=wage;sec.retained+=retain;sec.total_profit+=profit
  initiator=None
  if primary:
   day=today()
   if sec.day!=day:sec.day=day;sec.day_units=0
   sec.day_units+=1
   if sec.day_units>BASE_CAPACITY:
    for h in s.query(self.Hire).filter_by(sector=key,active=1).order_by(self.Hire.slot_start).all():
     if h.slot_start < sec.day_units <= h.slot_end:
      initiator=h.initiator_id
      break
  self.owner_pay(s,key,profit,initiator)

 def purchase(self,s,c,d,sku,qty=1,price_override=None):
  if sku not in ITEMS or qty not in (1,2,3,4):raise HTTPException(400,'Нет такого товара или количества')
  item=ITEMS[sku]
  # Supply capacity only limits optional high-demand goods; essential food/water remain available.
  key=item['sector']
  if sku not in ('food','water'):
   sec=s.get(self.Sector,key)
   day_n=sec.day_units if sec.day==today() else 0
   cap=BASE_CAPACITY+s.query(self.Worker).filter_by(sector=key).count()*CAPACITY_PER_HIRE
   if day_n+qty>cap:raise HTTPException(409,'Товар закончился: предприятию нужны работники')
  total=units(item['price']*qty) if price_override is None else units(price_override)
  bal=units(d.get('balance',0))
  if bal<total:raise HTTPException(400,'Недостаточно ЖН')
  d['balance']=amount(bal-total)
  for state_key in ('food','water','energy'):
   d[state_key]=round(min(100,float(d.get(state_key,0))+item[state_key]*qty),5)
  tax=total*8//100  # exactly 8%, integer micro-units
  rest=total-tax
  ctrl=self.locked(s);ctrl.treasury+=tax
  spent=tax
  for supplier,ratio in item['suppliers'].items():
   paid=int(total*ratio)
   self.sector_receive(s,supplier,paid,primary=True)
   spent+=paid
  self.sector_receive(s,key,total-spent,primary=True)
  self.fill_waitlist(s,key)
  self.add_log(s,c.account_id,-amount(total),'Покупка: '+item['name'])
  self.save(c,d)

 def credit_automatic(self,s,spent):
  # Existing automatic food/water purchases are an accounted transfer to the state shop.
  n=units(spent)
  if n:self.sector_receive(s,'retail',n)

 def buy_share(self,s,c,d,key):
  if key not in SECTOR_MAP:raise HTTPException(400,'Неизвестная отрасль')
  info=SECTOR_MAP[key]; price=units(info[2]);bal=units(d.get('balance',0))
  if bal<price:raise HTTPException(400,'Недостаточно ЖН')
  d['balance']=amount(bal-price)
  h=s.query(self.Holding).filter_by(account_id=c.account_id,sector=key).one_or_none()
  if h is None:s.add(self.Holding(account_id=c.account_id,sector=key,shares=1))
  else:h.shares+=1
  self.locked(s).treasury+=price
  self.add_log(s,c.account_id,-amount(price),'Покупка бизнеса: '+info[1])
  self.save(c,d)

 def finance_payroll(self,s,c,d,key,zhn):
  if key not in SECTOR_MAP:raise HTTPException(400,'Нет такого бизнеса')
  if not s.query(self.Holding).filter_by(account_id=c.account_id,sector=key).first():
   raise HTTPException(403,'Пополнить фонд может только совладелец')
  n=units(zhn)
  if n<units(1) or n>units(10000):raise HTTPException(400,'Сумма от 1 до 10000 ЖН')
  if n>units(d.get('balance',0)):raise HTTPException(400,'Недостаточно ЖН')
  d['balance']=amount(units(d.get('balance',0))-n)
  s.get(self.Sector,key).wage_pool+=n
  self.add_log(s,c.account_id,-amount(n),'Вклад в фонд зарплат: '+SECTOR_MAP[key][1])
  self.save(c,d)

 def job_slots(self,s,key):
  sector=s.get(self.Sector,key)
  active=s.query(self.Worker).filter_by(sector=key).count()
  # Funds are reserved for at least seven days of the minimum promised target payout.
  reserved=sector.wage_pool+sector.retained//2
  capacity=max(0,min(200, reserved//units(7*TARGET_DAILY_WAGE)))
  return int(capacity),int(active)

 def hire(self,s,c,d,key,initiator=None):
  if key not in SECTOR_MAP:raise HTTPException(400,'Нет такой профессии')
  if not d.get('alive',True) or self.age_at(d)<18:raise HTTPException(400,'Работа доступна живому персонажу с 18 лет')
  slots,active=self.job_slots(s,key)
  if active>=slots:raise HTTPException(409,'Свободных вакансий пока нет — можно встать в очередь')
  old=s.get(self.Worker,c.account_id)
  if old:
   for h in s.query(self.Hire).filter_by(worker_id=c.account_id,active=1).all():h.active=0
   s.delete(old);s.flush()
  last=s.query(self.Hire).filter_by(sector=key,active=1).order_by(self.Hire.slot_end.desc()).first()
  start=max(BASE_CAPACITY,last.slot_end if last else BASE_CAPACITY)
  w=self.Worker(account_id=c.account_id,sector=key,joined_day=today(),joined_at=time.time())
  s.add(w)
  s.add(self.Hire(worker_id=c.account_id,initiator_id=initiator,sector=key,slot_start=start,slot_end=start+CAPACITY_PER_HIRE))
  d['job']=SECTOR_MAP[key][3];d['employer']='sector'
  self.save(c,d)
  for q in s.query(self.Queue).filter_by(account_id=c.account_id).all():s.delete(q)
  return w

 def queue(self,s,c,d,key):
  if key not in SECTOR_MAP:raise HTTPException(400,'Нет такой профессии')
  if self.age_at(d)<18 or not d.get('alive',True):raise HTTPException(400,'Заявка доступна с 18 лет')
  q=s.query(self.Queue).filter_by(account_id=c.account_id,sector=key).one_or_none()
  if q is None:s.add(self.Queue(account_id=c.account_id,sector=key,applied_day=today()))

 def dismiss(self,s,owner,sector,worker_id):
  if sector not in SECTOR_MAP:raise HTTPException(400,'Нет такого бизнеса')
  holding=s.query(self.Holding).filter_by(account_id=owner,sector=sector).one_or_none()
  if holding is None:raise HTTPException(403,'Увольнять может только совладелец')
  worker=s.get(self.Worker,worker_id)
  if not worker or worker.sector!=sector:raise HTTPException(404,'Сотрудник не найден')
  if worker_id==owner:raise HTTPException(400,'Чтобы уйти с работы, нажми «Уволиться»')
  target=self.get_char(s,worker_id)
  d=json.loads(target.state)
  self.leave_job(s,target,d)
  self.add_log(s,worker_id,0,'Уволен из '+SECTOR_MAP[sector][1])
  return True

 def leave_job(self,s,c,d):
  old=s.get(self.Worker,c.account_id)
  if old:
   for h in s.query(self.Hire).filter_by(worker_id=c.account_id,active=1).all():h.active=0
   s.delete(old)
  d['job']=None;d['employer']='state';self.save(c,d)

 def fill_waitlist(self,s,key):
  slots,active=self.job_slots(s,key)
  for q in s.query(self.Queue).filter_by(sector=key).order_by(self.Queue.applied_day,self.Queue.id).all():
   if active>=slots:break
   c=self.get_char(s,q.account_id);d=json.loads(c.state)
   if self.age_at(d)<18 or not d.get('alive',True):s.delete(q);continue
   self.hire(s,c,d,key,initiator=None)
   active+=1

 def rollover(self,s):
  ctrl=self.locked(s)
  target=today()
  if ctrl.last_payroll>=target and ctrl.last_interest>=target:return
  # Cap missed days; no retroactive wage creation or promise from empty sector funds.
  if ctrl.last_payroll<target:
   for sec in s.query(self.Sector).order_by(self.Sector.key).all():
    workers=s.query(self.Worker).filter_by(sector=sec.key).order_by(self.Worker.account_id).all()
    # Time-weighted payroll prevents gaining a whole daily wage after one minute.
    eligible=[]
    period_start=(target-1)*86400
    period_end=target*86400
    for w in workers:
     time_fraction=max(0,min(1,(period_end-max(w.joined_at,period_start))/86400))
     if time_fraction>0:eligible.append((w,units(TARGET_DAILY_WAGE*time_fraction)))
    demand=sum(x for _,x in eligible)
    payroll=min(sec.wage_pool,demand)
    paid=0
    if demand>0:
     for w,need in eligible:
      alloc=payroll*need//demand
      if alloc:self.pending_credit(s,w.account_id,alloc);paid+=alloc
     sec.wage_pool-=paid
     sec.total_payroll+=paid
    self.fill_waitlist(s,sec.key)
   ctrl.last_payroll=target
  if ctrl.last_interest<target:
   loans=s.query(self.Loan).all()
   for loan in loans:
    if loan.principal>0:
     loan.interest_due+=max(1,loan.principal//100)
    if loan.interest_due>0:
     c=self.get_char(s,loan.account_id)
     d=json.loads(c.state)
     payment=min(max(0,units(d.get('balance',0))),loan.interest_due)
     if payment:
      d['balance']=amount(units(d.get('balance',0))-payment)
      self.save(c,d)
      loan.interest_due-=payment
      ctrl.bank_earnings+=payment
      self.add_log(s,loan.account_id,-amount(payment),'Автосписание процентов по кредиту')
   # Deposit interest is funded solely by the bank's actual earnings; interest
   # is paid pro rata when available and does not create money.
   deposits=s.query(self.Deposit).filter(self.Deposit.principal>0).order_by(self.Deposit.account_id).all()
   planned=[max(1,int(d.principal*.0025)) for d in deposits]
   required=sum(planned);paid=min(required,ctrl.bank_earnings)
   if required and paid:
    credited=0
    for d,x in zip(deposits,planned):
     y=paid*x//required;d.principal+=y;credited+=y
    ctrl.bank_earnings-=credited
   ctrl.last_interest=target

 def daily_bonus(self,s,c,d):
  day=today()
  if int(d.get('econ_login_bonus_day',-1))==day:return False
  ctrl=self.locked(s)
  if ctrl.treasury<units(1):return False
  ctrl.treasury-=units(1)
  d['balance']=amount(units(d.get('balance',0))+units(1))
  d['econ_login_bonus_day']=day
  self.add_log(s,c.account_id,1,'Ежедневный бонус из казны')
  self.save(c,d)
  return True

 def deposit(self,s,c,d,zhn):
  n=units(zhn)
  if n<=0 or n>units(100000):raise HTTPException(400,'Неверная сумма')
  if units(d.get('balance',0))<n:raise HTTPException(400,'Недостаточно ЖН')
  d['balance']=amount(units(d.get('balance',0))-n)
  dep=s.get(self.Deposit,c.account_id)
  if dep is None:dep=self.Deposit(account_id=c.account_id,principal=0);s.add(dep)
  dep.principal+=n;self.save(c,d);self.add_log(s,c.account_id,-zhn,'Виртуальный депозит')

 def withdraw_deposit(self,s,c,d,zhn):
  n=units(zhn);dep=s.get(self.Deposit,c.account_id)
  if not dep or n<=0 or n>dep.principal:raise HTTPException(400,'Недостаточный депозит')
  dep.principal-=n;d['balance']=amount(units(d.get('balance',0))+n)
  self.save(c,d);self.add_log(s,c.account_id,zhn,'Возврат виртуального депозита')

 def bank_credit(self,s,c,d,zhn):
  if self.age_at(d)<30:raise HTTPException(400,'Кредит доступен с 30 игровых лет')
  n=units(zhn);ctrl=self.locked(s)
  if n<=0 or n>ctrl.bank_capital:raise HTTPException(400,'В кредитном фонде недостаточно денег')
  if s.get(self.Loan,c.account_id) and s.get(self.Loan,c.account_id).principal>0:raise HTTPException(409,'Сначала погаси действующий кредит')
  # Income-based ceiling; bonuses or player-to-player transfers are not qualifying salary.
  end=datetime.now(timezone.utc).replace(tzinfo=None)
  past=s.query(self.Ledger).filter_by(account_id=c.account_id).order_by(self.Ledger.id.desc()).limit(100).all()
  recent=sum(max(0,x.amount) for x in past if (x.reason or '').startswith('Дивиденды и зарплаты') and (end-x.created.replace(tzinfo=None)).total_seconds()<7*86400)
  avg=recent/7
  limit=units(max(0,avg*20))
  if n>limit:raise HTTPException(400,'Недостаточно подтверждённого дохода для кредита')
  loan=s.get(self.Loan,c.account_id)
  if loan is None:loan=self.Loan(account_id=c.account_id,principal=0,interest_due=0);s.add(loan)
  loan.principal=n;loan.interest_due=0;ctrl.bank_capital-=n
  d['balance']=amount(units(d.get('balance',0))+n);self.save(c,d)
  self.add_log(s,c.account_id,zhn,'Получен виртуальный кредит')

 def loan_pay(self,s,c,d,zhn):
  n=units(zhn);loan=s.get(self.Loan,c.account_id)
  if not loan or loan.principal+loan.interest_due<=0:raise HTTPException(400,'Нет кредита')
  if n<=0 or n>units(d.get('balance',0)):raise HTTPException(400,'Недостаточно денег')
  n=min(n,loan.principal+loan.interest_due)
  interest=min(n,loan.interest_due);body=n-interest
  loan.interest_due-=interest;loan.principal-=body
  ctrl=self.locked(s);ctrl.bank_earnings+=interest;ctrl.bank_capital+=body
  d['balance']=amount(units(d.get('balance',0))-n);self.save(c,d)
  self.add_log(s,c.account_id,-amount(n),'Погашение виртуального кредита')

 def dashboard(self,s,aid):
  ctrl=s.get(self.Control,1)
  holders=s.query(self.Holding).filter_by(account_id=aid).all()
  worker=s.get(self.Worker,aid)
  queue=s.query(self.Queue).filter_by(account_id=aid).all()
  dep=s.get(self.Deposit,aid);loan=s.get(self.Loan,aid)
  sectors=[]
  for key,name,price,job,product in SECTORS:
   sec=s.get(self.Sector,key)
   sector_holdings=s.query(self.Holding).filter_by(sector=key).all()
   shares=sum(h.shares for h in sector_holdings)
   owners_count=sum(1 for h in sector_holdings if h.shares>0)
   own=sum(h.shares for h in holders if h.sector==key)
   vacancies,staff=self.job_slots(s,key)
   pending=sec.wage_pool if sec else 0
   sectors.append({'key':key,'name':name,'price':price,'job':job,'product':product,'owners':shares,'owners_count':owners_count,'my_shares':own,
    'my_percent':round(100*own/shares,2) if shares else 0,'available_jobs':max(0,vacancies-staff),
    'workers':staff,'estimated_wage':round(min(TARGET_DAILY_WAGE,amount(pending)/max(1,staff+1)),2),
    'sales_total':amount(sec.total_revenue),'profit_total':amount(sec.total_profit),'wage_fund':amount(sec.wage_pool),
    'reserve':amount(sec.retained)})
  return {'currency':'ЖН','treasury':amount(ctrl.treasury),'minted':amount(ctrl.minted),'burned':amount(ctrl.burned),
   'real_reserve_uah':None,'real_exchange_rate':None,'real_withdrawals_enabled':False,
   'login_bonus':1,'login_bonus_funded':ctrl.treasury>=units(1),
   'my_job':worker.sector if worker else None,'queued':[{'sector':q.sector,'position':s.query(self.Queue).filter(self.Queue.sector==q.sector,self.Queue.id<=q.id).count()} for q in queue],
   'deposit':amount(dep.principal) if dep else 0,
   'loan':amount(loan.principal) if loan else 0,'interest_due':amount(loan.interest_due) if loan else 0,
   'bank_credit_available':amount(ctrl.bank_capital),'bank_interest_reserve':amount(ctrl.bank_earnings),
   'items':[{'id':k,**v} for k,v in ITEMS.items()], 'sectors':sectors}

 def register_routes(self,app,get_auth,settle,state_payload):
  svc=self
  class EconAction(BaseModel):
   type:str=Field(min_length=1,max_length=32)
   sector:str|None=None
   item:str|None=None
   quantity:int=1
   amount:float=0.0
   target_id:int|None=None
  @app.get('/api/economy')
  def get_economy(aid:int=Depends(get_auth)):
   with svc.Session.begin() as s:
    svc.rollover(s)
    return svc.dashboard(s,aid)
  @app.post('/api/economy/action')
  def econ_action(body:EconAction,aid:int=Depends(get_auth)):
   with svc.Session.begin() as s:
    svc.rollover(s)
    c=svc.get_char(s,aid)
    d=settle(s,c)
    svc.migrate(s,c,d)
    svc.claim(s,c,d)
    if not d.get('alive',True):raise HTTPException(400,'Персонаж умер')
    typ=body.type
    if typ=='buy':svc.purchase(s,c,d,body.item or '',body.quantity)
    elif typ=='share':svc.buy_share(s,c,d,body.sector or '')
    elif typ=='queue':svc.queue(s,c,d,body.sector or '')
    elif typ=='apply':
     key=body.sector or ''
     try:svc.hire(s,c,d,key)
     except HTTPException as ex:
      if ex.status_code!=409:raise
      svc.queue(s,c,d,key)
    elif typ=='leave':svc.leave_job(s,c,d)
    elif typ=='cancel_queue':
     for q in s.query(svc.Queue).filter_by(account_id=aid,sector=body.sector).all():s.delete(q)
    elif typ=='fire':svc.dismiss(s,aid,body.sector or '',body.target_id or 0)
    elif typ=='hire':
     if body.sector not in SECTOR_MAP:raise HTTPException(400,'Нет такого бизнеса')
     own=s.query(svc.Holding).filter_by(account_id=aid,sector=body.sector).one_or_none()
     if not own:raise HTTPException(403,'Нанимать может только совладелец отрасли')
     if not body.target_id or body.target_id==aid:raise HTTPException(400,'Укажи ID кандидата из очереди')
     q=s.query(svc.Queue).filter_by(account_id=body.target_id,sector=body.sector).first()
     if not q:raise HTTPException(404,'Кандидата нет в очереди')
     first=s.query(svc.Queue).filter_by(sector=body.sector).order_by(svc.Queue.applied_day,svc.Queue.id).first()
     if first and first.id!=q.id:raise HTTPException(409,'Сначала кандидат, который раньше встал в очередь')
     target=svc.get_char(s,body.target_id);td=json.loads(target.state)
     svc.hire(s,target,td,body.sector,initiator=aid)
     s.delete(q)
    elif typ=='finance':svc.finance_payroll(s,c,d,body.sector or '',body.amount)
    elif typ=='deposit':svc.deposit(s,c,d,body.amount)
    elif typ=='undeposit':svc.withdraw_deposit(s,c,d,body.amount)
    elif typ=='credit':svc.bank_credit(s,c,d,body.amount)
    elif typ=='repay':svc.loan_pay(s,c,d,body.amount)
    else:raise HTTPException(400,'Неизвестная операция')
    svc.save(c,d)
    return {'ok':True,'player':state_payload(s,s.get(svc.Account,aid),c,d),'economy':svc.dashboard(s,aid)}
  @app.get('/api/economy/workers/{sector}')
  def workers(sector:str,aid:int=Depends(get_auth)):
   if sector not in SECTOR_MAP:raise HTTPException(404,'Отрасль не найдена')
   with svc.Session() as s:
    if not s.query(svc.Holding).filter_by(account_id=aid,sector=sector).first():raise HTTPException(403,'Только для владельцев')
    return {'sector':sector,'workers':[{'id':w.account_id,'day':w.joined_day} for w in s.query(svc.Worker).filter_by(sector=sector).order_by(svc.Worker.joined_day,svc.Worker.account_id).limit(100).all()]}

  @app.get('/api/economy/queue/{sector}')
  def waiting(sector:str,aid:int=Depends(get_auth)):
   if sector not in SECTOR_MAP:raise HTTPException(404,'Отрасль не найдена')
   with svc.Session() as s:
    own=s.query(svc.Holding).filter_by(account_id=aid,sector=sector).one_or_none()
    if not own:raise HTTPException(403,'Только для владельцев')
    records=s.query(svc.Queue).filter_by(sector=sector).order_by(svc.Queue.applied_day,svc.Queue.id).limit(30).all()
    return {'sector':sector,'candidates':[{'id':q.account_id,'since':q.applied_day} for q in records]}
