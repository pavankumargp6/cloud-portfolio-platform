from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.application import Application
from app.models.client import Client
from app.schemas.application import (
    ApplicationCreate,
    ApplicationRead,
    ApplicationUpdate,
)
from app.schemas.pagination import Page

router = APIRouter(tags=["applications"])


def _require_client(db: Session, client_id: int) -> Client:
    client = db.get(Client, client_id)
    if client is None:
        raise HTTPException(status_code=404, detail="Client not found")
    return client


def _require_application(db: Session, application_id: int) -> Application:
    application = db.get(Application, application_id)
    if application is None:
        raise HTTPException(status_code=404, detail="Application not found")
    return application


@router.post(
    "/clients/{client_id}/applications",
    response_model=ApplicationRead,
    status_code=201,
)
def create_application(
    client_id: int, payload: ApplicationCreate, db: Session = Depends(get_db)
):
    _require_client(db, client_id)
    application = Application(client_id=client_id, **payload.model_dump())
    db.add(application)
    db.commit()
    db.refresh(application)
    return application


@router.get(
    "/clients/{client_id}/applications", response_model=Page[ApplicationRead]
)
def list_client_applications(
    client_id: int,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    _require_client(db, client_id)
    total = db.scalar(
        select(func.count())
        .select_from(Application)
        .where(Application.client_id == client_id)
    ) or 0
    rows = db.scalars(
        select(Application)
        .where(Application.client_id == client_id)
        .order_by(Application.id)
        .limit(limit)
        .offset(offset)
    ).all()
    return {"items": rows, "total": total, "limit": limit, "offset": offset}


@router.get("/applications", response_model=Page[ApplicationRead])
def list_applications(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    total = db.scalar(select(func.count()).select_from(Application)) or 0
    rows = db.scalars(
        select(Application).order_by(Application.id).limit(limit).offset(offset)
    ).all()
    return {"items": rows, "total": total, "limit": limit, "offset": offset}


@router.get("/applications/{application_id}", response_model=ApplicationRead)
def get_application(application_id: int, db: Session = Depends(get_db)):
    return _require_application(db, application_id)


@router.patch("/applications/{application_id}", response_model=ApplicationRead)
def update_application(
    application_id: int,
    payload: ApplicationUpdate,
    db: Session = Depends(get_db),
):
    application = _require_application(db, application_id)

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(application, field, value)

    db.commit()
    db.refresh(application)
    return application


@router.delete("/applications/{application_id}", status_code=204)
def delete_application(application_id: int, db: Session = Depends(get_db)):
    application = _require_application(db, application_id)
    db.delete(application)
    db.commit()
    return Response(status_code=204)
