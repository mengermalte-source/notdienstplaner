from pathlib import Path
from fastapi import APIRouter, Depends, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select
from datetime import date as date_type, datetime
from app.database import get_session
from app.deps import get_current_user, require_admin
from app.models.user import User
from app.models.swap import SwapRequest, SwapStatus
from app.models.schedule import ShiftAssignment

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")


@router.get("/me/swaps", response_class=HTMLResponse)
async def swaps_page(request: Request, user: User = Depends(get_current_user),
                     session: AsyncSession = Depends(get_session)):
    my_swaps = (await session.exec(
        select(SwapRequest).where(
            (SwapRequest.requester_id == user.id) | (SwapRequest.target_id == user.id)
        ).order_by(SwapRequest.created_at.desc()))).all()
    users = {u.id: u for u in (await session.exec(select(User))).all()}
    my_shifts = (await session.exec(
        select(ShiftAssignment).where(ShiftAssignment.user_id == user.id)
        .order_by(ShiftAssignment.date))).all()

    return templates.TemplateResponse("doctor/swaps.html", {
        "request": request, "user": user, "swaps": my_swaps,
        "users": users, "my_shifts": my_shifts,
    })


@router.get("/me/swaps/pending-count")
async def pending_swap_count(user: User = Depends(get_current_user),
                              session: AsyncSession = Depends(get_session)):
    count = len((await session.exec(
        select(SwapRequest).where(
            SwapRequest.target_id == user.id,
            SwapRequest.status == SwapStatus.pending,
        )
    )).all())
    if count > 0:
        return HTMLResponse(
            f'<span class="ml-auto bg-rose-500 text-white text-[10px] font-bold '
            f'rounded-full min-w-[18px] h-[18px] flex items-center justify-center px-1">'
            f'{count}</span>'
        )
    return HTMLResponse("")


@router.post("/me/swaps/request")
async def request_swap(user: User = Depends(get_current_user),
                        target_id: int = Form(...),
                        my_date: str = Form(...),
                        their_date: str = Form(...),
                        message: str = Form(""),
                        session: AsyncSession = Depends(get_session)):
    my_shift = (await session.exec(
        select(ShiftAssignment).where(
            ShiftAssignment.user_id == user.id,
            ShiftAssignment.date == date_type.fromisoformat(my_date),
        ))).first()
    their_shift = (await session.exec(
        select(ShiftAssignment).where(
            ShiftAssignment.user_id == target_id,
            ShiftAssignment.date == date_type.fromisoformat(their_date),
        ))).first()
    if not my_shift or not their_shift:
        raise HTTPException(status_code=400, detail="Ungültige Dienstdaten")

    session.add(SwapRequest(
        requester_id=user.id, target_id=target_id,
        requester_shift_date=my_shift.date, target_shift_date=their_shift.date,
        planning_period_id=my_shift.planning_period_id, message=message,
    ))
    await session.commit()
    return RedirectResponse("/me/swaps", status_code=302)


@router.post("/me/swaps/{swap_id}/accept")
async def accept_swap(swap_id: int, user: User = Depends(get_current_user),
                       session: AsyncSession = Depends(get_session)):
    swap = await session.get(SwapRequest, swap_id)
    if not swap or swap.target_id != user.id:
        raise HTTPException(status_code=403)
    swap.status = SwapStatus.accepted
    session.add(swap)
    await session.commit()
    return RedirectResponse("/me/swaps", status_code=302)


@router.post("/admin/swaps/{swap_id}/approve")
async def admin_approve_swap(swap_id: int, _: User = Depends(require_admin),
                              session: AsyncSession = Depends(get_session)):
    swap = await session.get(SwapRequest, swap_id)
    if not swap or swap.status != SwapStatus.accepted:
        raise HTTPException(status_code=400,
                            detail="Tausch muss erst vom Zielarzt akzeptiert sein")

    a1 = (await session.exec(select(ShiftAssignment).where(
        ShiftAssignment.user_id == swap.requester_id,
        ShiftAssignment.date == swap.requester_shift_date))).first()
    a2 = (await session.exec(select(ShiftAssignment).where(
        ShiftAssignment.user_id == swap.target_id,
        ShiftAssignment.date == swap.target_shift_date))).first()

    if a1 and a2:
        a1.user_id, a2.user_id = a2.user_id, a1.user_id
        a1.is_manual_override = True
        a2.is_manual_override = True
        session.add(a1)
        session.add(a2)

    swap.status = SwapStatus.admin_approved
    swap.resolved_at = datetime.utcnow()
    session.add(swap)
    await session.commit()
    return RedirectResponse("/admin/planning", status_code=302)
