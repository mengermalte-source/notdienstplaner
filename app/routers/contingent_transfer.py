from pathlib import Path
from datetime import datetime
from fastapi import APIRouter, Depends, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select

from app.database import get_session
from app.deps import get_current_user
from app.models.user import User, DoctorProfile, UserRole
from app.models.contingent_transfer import ContingentTransfer, TransferStatus

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")


@router.get("/me/transfers", response_class=HTMLResponse)
async def transfers_page(
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    transfers = (await session.exec(
        select(ContingentTransfer).where(
            (ContingentTransfer.sender_id == user.id)
            | (ContingentTransfer.receiver_id == user.id)
        ).order_by(ContingentTransfer.created_at.desc())
    )).all()

    all_doctors = (await session.exec(
        select(User).where(User.role == UserRole.doctor, User.is_active == True)
    )).all()
    users_map = {u.id: u for u in all_doctors}

    my_profile = (await session.exec(
        select(DoctorProfile).where(DoctorProfile.user_id == user.id)
    )).first()

    incoming_pending = [
        t for t in transfers
        if t.receiver_id == user.id and t.status == TransferStatus.pending
    ]
    active = [
        t for t in transfers
        if t.status == TransferStatus.accepted
    ]

    error = request.query_params.get("error")

    return templates.TemplateResponse("doctor/transfers.html", {
        "request": request,
        "user": user,
        "transfers": transfers,
        "incoming_pending": incoming_pending,
        "active": active,
        "users_map": users_map,
        "all_doctors": all_doctors,
        "my_profile": my_profile,
        "error": error,
    })


@router.get("/me/transfers/pending-count")
async def pending_transfer_count(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    count = len((await session.exec(
        select(ContingentTransfer).where(
            ContingentTransfer.receiver_id == user.id,
            ContingentTransfer.status == TransferStatus.pending,
        )
    )).all())
    if count > 0:
        return HTMLResponse(
            f'<span class="ml-auto bg-rose-500 text-white text-[10px] font-bold '
            f'rounded-full min-w-[18px] h-[18px] flex items-center justify-center px-1">'
            f'{count}</span>'
        )
    return HTMLResponse("")


@router.post("/me/transfers")
async def create_transfer(
    receiver_id: int = Form(...),
    percent: float = Form(...),
    message: str = Form(""),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    if receiver_id == user.id:
        return RedirectResponse("/me/transfers?error=Ungültiger+Empfänger", status_code=302)

    if percent <= 0.0 or percent > 100.0:
        return RedirectResponse("/me/transfers?error=Prozent+muss+zwischen+1+und+100+liegen", status_code=302)

    profile = (await session.exec(
        select(DoctorProfile).where(DoctorProfile.user_id == user.id)
    )).first()
    if not profile:
        return RedirectResponse("/me/transfers?error=Kein+Profil+gefunden", status_code=302)

    if profile.credit_factor - percent / 100 < 0.0:
        return RedirectResponse(
            "/me/transfers?error=Nicht+genügend+Kontingent+verfügbar", status_code=302
        )

    existing_pending = (await session.exec(
        select(ContingentTransfer).where(
            ContingentTransfer.sender_id == user.id,
            ContingentTransfer.receiver_id == receiver_id,
            ContingentTransfer.status == TransferStatus.pending,
        )
    )).first()
    if existing_pending:
        return RedirectResponse(
            "/me/transfers?error=Es+existiert+bereits+ein+offener+Antrag+an+diese+Person",
            status_code=302,
        )

    session.add(ContingentTransfer(
        sender_id=user.id,
        receiver_id=receiver_id,
        percent=percent,
        message=message,
    ))
    await session.commit()
    return RedirectResponse("/me/transfers", status_code=302)


@router.post("/me/transfers/{transfer_id}/accept")
async def accept_transfer(
    transfer_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    transfer = await session.get(ContingentTransfer, transfer_id)
    if not transfer or transfer.receiver_id != user.id or transfer.status != TransferStatus.pending:
        raise HTTPException(status_code=403)

    sender_profile = (await session.exec(
        select(DoctorProfile).where(DoctorProfile.user_id == transfer.sender_id)
    )).first()
    receiver_profile = (await session.exec(
        select(DoctorProfile).where(DoctorProfile.user_id == transfer.receiver_id)
    )).first()
    if not sender_profile or not receiver_profile:
        raise HTTPException(status_code=400, detail="Profil nicht gefunden")

    delta = transfer.percent / 100
    sender_profile.credit_factor = round(sender_profile.credit_factor - delta, 10)
    receiver_profile.credit_factor = round(receiver_profile.credit_factor + delta, 10)
    transfer.status = TransferStatus.accepted
    transfer.resolved_at = datetime.utcnow()

    session.add(sender_profile)
    session.add(receiver_profile)
    session.add(transfer)
    await session.commit()
    return RedirectResponse("/me/transfers", status_code=302)


@router.post("/me/transfers/{transfer_id}/reject")
async def reject_transfer(
    transfer_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    transfer = await session.get(ContingentTransfer, transfer_id)
    if not transfer or transfer.receiver_id != user.id or transfer.status != TransferStatus.pending:
        raise HTTPException(status_code=403)

    transfer.status = TransferStatus.rejected
    transfer.resolved_at = datetime.utcnow()
    session.add(transfer)
    await session.commit()
    return RedirectResponse("/me/transfers", status_code=302)


@router.post("/me/transfers/{transfer_id}/revoke")
async def revoke_transfer(
    transfer_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    transfer = await session.get(ContingentTransfer, transfer_id)
    if (
        not transfer
        or (transfer.sender_id != user.id and transfer.receiver_id != user.id)
        or transfer.status != TransferStatus.accepted
    ):
        raise HTTPException(status_code=403)

    sender_profile = (await session.exec(
        select(DoctorProfile).where(DoctorProfile.user_id == transfer.sender_id)
    )).first()
    receiver_profile = (await session.exec(
        select(DoctorProfile).where(DoctorProfile.user_id == transfer.receiver_id)
    )).first()
    if not sender_profile or not receiver_profile:
        raise HTTPException(status_code=400, detail="Profil nicht gefunden")

    delta = transfer.percent / 100
    sender_profile.credit_factor = round(sender_profile.credit_factor + delta, 10)
    receiver_profile.credit_factor = round(receiver_profile.credit_factor - delta, 10)
    transfer.status = TransferStatus.revoked
    transfer.revoked_at = datetime.utcnow()
    transfer.revoked_by_id = user.id

    session.add(sender_profile)
    session.add(receiver_profile)
    session.add(transfer)
    await session.commit()
    return RedirectResponse("/me/transfers", status_code=302)
