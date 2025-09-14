from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Form, status
from fastapi.security.api_key import APIKeyHeader
from fastapi import Response
from typing import List, Optional
from pydantic import BaseModel, EmailStr
from sqlalchemy import create_engine, Column, Integer, String, LargeBinary, Enum
from sqlalchemy.orm import sessionmaker, declarative_base, Session
from sqlalchemy.dialects.postgresql import UUID
import enum
import uuid
import hashlib

# Configurations
ATTORNEY_EMAIL = "attorney@yourfirm.com"
INTERNAL_API_KEY = "SECRET_INTERNAL_KEY"  # Change in production

# Database Setup
SQLALCHEMY_DATABASE_URL = "sqlite:///./leads.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class LeadState(enum.Enum):
    PENDING = "PENDING"
    REACHED_OUT = "REACHED_OUT"

class Lead(Base):
    __tablename__ = "leads"
    id = Column(String, primary_key=True, index=True)
    first_name = Column(String, nullable=False)
    last_name = Column(String, nullable=False)
    email = Column(String, nullable=False)
    resume_filename = Column(String, nullable=False)
    resume_data = Column(LargeBinary, nullable=False)
    state = Column(Enum(LeadState), default=LeadState.PENDING)

Base.metadata.create_all(bind=engine)

# API Models
class LeadCreate(BaseModel):
    first_name: str
    last_name: str
    email: EmailStr

class LeadOut(BaseModel):
    id: str
    first_name: str
    last_name: str
    email: EmailStr
    resume_filename: str
    state: LeadState
   
    class Config:
        orm_mode = True
        allow_population_by_field_name = True

class LeadStateUpdate(BaseModel):
    state: LeadState

# Dependency: DB Session
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def create_uuid_from_string(val: str):
    hex_string = hashlib.md5(val.encode("UTF-8")).hexdigest()
    return str(uuid.UUID(hex=hex_string))

# Dependency: Auth
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
def get_api_key(api_key: Optional[str] = Depends(api_key_header)):
    if api_key != INTERNAL_API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")

# Dummy Email Sender
def send_email(to: str, subject: str, body: str):
    # In production, integrate with real email service like SMTP, SendGrid, etc.
    print(f"Sending EMAIL to: {to}\nSubject: {subject}\n{body}\n")

# FastAPI App
app = FastAPI()

# Public endpoint: Submit a lead
@app.post("/leads", response_model=LeadOut | str)
async def create_lead(
    first_name: str = Form(...),
    last_name: str = Form(...),
    email: EmailStr = Form(...),
    resume: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    if not resume.filename.lower().endswith(".pdf"):
        return "file error: only pdf file of resume is allowed."
    email = email.lower()
    lead_id = create_uuid_from_string(email)
    if db.query(Lead).filter(Lead.id == lead_id).first():
        return "you have already applied. you can update your resume"
    file_data = await resume.read()
    lead = Lead(
        id=lead_id,
        first_name=first_name,
        last_name=last_name,
        email=email.lower(),
        resume_filename=resume.filename,
        resume_data=file_data,
        state=LeadState.PENDING
    )
    db.add(lead)
    db.commit()
    db.refresh(lead)

    # Send email to prospect
    send_email(
        to=email,
        subject="Thank you for your submission!",
        body=f"Dear {first_name},\n\nThank you for submitting your lead. We will contact you soon.\n"
    )

    # Send email to attorney
    send_email(
        to=ATTORNEY_EMAIL,
        subject="New Lead Submitted",
        body=f"Lead from {first_name} {last_name} ({email}) received."
    )

    return LeadOut.from_orm(lead)

# Internal API: List all leads (auth required)
@app.get("/leads", response_model=List[LeadOut], dependencies=[Depends(get_api_key)])
def get_leads(db: Session = Depends(get_db)):
    return [LeadOut.from_orm(lead) for lead in db.query(Lead).order_by(Lead.id.desc()).all()]

# Internal API: Get a specific lead (auth required)
@app.get("/leads/{lead_id}", response_model=LeadOut, dependencies=[Depends(get_api_key)])
def get_lead(lead_id: str, db: Session = Depends(get_db)):
    lead = db.query(Lead).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    return LeadOut.from_orm(lead)

# Internal API: Download resume (auth required)
@app.get("/leads/{lead_id}/resume", dependencies=[Depends(get_api_key)])
def download_resume(lead_id: str, db: Session = Depends(get_db)):
    lead = db.query(Lead).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    return Response(
        lead.resume_data,
        headers = {'Content-Disposition': f'attachment; filename="{lead.resume_filename}"'},
        media_type="application/pdf",
    )

# Internal API: update resume (auth required)
@app.put("/leads/{lead_id}/resume", response_model=LeadOut, dependencies=[Depends(get_api_key)])
async def update_resume(lead_id: str, resume: UploadFile = File(...), db: Session = Depends(get_db)):
    lead = db.query(Lead).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    lead.state = LeadState.PENDING
    lead.resume_filename = resume.filename
    file_data = await resume.read()
    lead.resume_date = file_data
    db.commit()
    db.refresh(lead)
    return LeadOut.from_orm(lead)

# Internal API: Update lead state (auth required)
@app.put("/leads/{lead_id}/state", response_model=LeadOut, dependencies=[Depends(get_api_key)])
def update_lead_state(lead_id: str, update: LeadStateUpdate, db: Session = Depends(get_db)):
    lead = db.query(Lead).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    lead.state = update.state
    db.commit()
    db.refresh(lead)
    return LeadOut.from_orm(lead)
