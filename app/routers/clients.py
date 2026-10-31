from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.application import Application
from app.models.client import Client
from app.schemas.client import ClientCreate, ClientRead, ClientUpdate
from app.schemas.pagination import Page

router = APIRouter(tags=["clients"])


@router.post("/clients", response_model=ClientRead, status_code=201)
def create_client(payload: ClientCreate, db: Session = Depends(get_db)):
    client = Client(
        name=payload.name,
        contact_email=payload.contact_email,
        notes=payload.notes,
    )
    db.add(client)
    db.commit()
    db.refresh(client)
    return client


@router.get("/clients", response_model=Page[ClientRead])
def list_clients(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    total = db.scalar(select(func.count()).select_from(Client)) or 0
    rows = db.scalars(
        select(Client).order_by(Client.id).limit(limit).offset(offset)
    ).all()
    return {"items": rows, "total": total, "limit": limit, "offset": offset}


@router.get("/clients/{client_id}", response_model=ClientRead)
def get_client(client_id: int, db: Session = Depends(get_db)):
    client = db.get(Client, client_id)
    if client is None:
        raise HTTPException(status_code=404, detail="Client not found")
    return client


@router.patch("/clients/{client_id}", response_model=ClientRead)
def update_client(
    client_id: int, payload: ClientUpdate, db: Session = Depends(get_db)
):
    client = db.get(Client, client_id)
    if client is None:
        raise HTTPException(status_code=404, detail="Client not found")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(client, field, value)

    db.commit()
    db.refresh(client)
    return client


@router.delete("/clients/{client_id}", status_code=204)
def delete_client(client_id: int, db: Session = Depends(get_db)):
    client = db.get(Client, client_id)
    if client is None:
        raise HTTPException(status_code=404, detail="Client not found")

    app_count = db.scalar(
        select(func.count())
        .select_from(Application)
        .where(Application.client_id == client_id)
    ) or 0
    if app_count > 0:
        raise HTTPException(
            status_code=409,
            detail=(
                "Client has associated applications; "
                "remove or archive them before deleting the client"
            ),
        )

    db.delete(client)
    db.commit()
    return Response(status_code=204)
