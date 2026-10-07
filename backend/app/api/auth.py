from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse, RedirectResponse
from urllib.parse import parse_qs
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user
from app.db.models import User
from app.db.session import get_db
from app.clients.bldcms import BLDCMSClient
from app.core.config import get_settings
from app.schemas.auth import CurrentUser, LoginRequest, TokenResponse
from app.services import handoff_bootstrap
from app.services.auth_service import login, login_with_handoff, to_current_user
from app.services.bldcms_client import get_bldcms_client

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
def login_route(payload: LoginRequest, db: Session = Depends(get_db)):
    return login(db, payload.employee_id, payload.password)


#: The cookie that carries the bootstrap id between POST / and GET /. HttpOnly so no script can
#: read it, SameSite=Lax so it is sent on the top-level redirect the browser follows but not on
#: a cross-site sub-request, and Path=/ because the SPA finalizes from the root.
BOOTSTRAP_COOKIE = "od_handoff_bootstrap"


def _origin_is_allowed(request: Request) -> bool:
    """The bootstrap POST arrives cross-origin from BL-DCMS, so its Origin is checked against a
    configured allow-list. Configuring none disables the check rather than blocking every
    handoff, which is the right default for a deployment where this endpoint is not reachable
    from outside the shed network - but a configured list is strictly better and is what the
    deployment notes ask for."""
    allowed = [
        o.strip().rstrip("/")
        for o in (get_settings().bldcms_browser_origins or "").split(",")
        if o.strip()
    ]
    if not allowed:
        return True
    origin = (request.headers.get("origin") or "").rstrip("/")
    if origin:
        return origin in allowed
    # Some browsers omit Origin on a form POST; fall back to Referer's scheme+host.
    referer = request.headers.get("referer") or ""
    return any(referer.startswith(f"{o}/") or referer == o for o in allowed)


async def _handoff_code_from_form(request: Request) -> str:
    """Read `handoff_code` out of an application/x-www-form-urlencoded body.

    Parsed here with urllib rather than through FastAPI's Form(...), which pulls in
    python-multipart - a dependency this deployment cannot install (no network). Doing it
    explicitly also means the accepted content type is stated rather than implied: this
    endpoint takes a plain form POST and nothing else.
    """
    content_type = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if content_type != "application/x-www-form-urlencoded":
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Expected a form submission.",
        )
    body = (await request.body()).decode("utf-8", errors="replace")
    values = parse_qs(body, keep_blank_values=False).get("handoff_code") or []
    code = values[0].strip() if values else ""
    if not code:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing handoff code.")
    return code


@router.post("/handoff/bootstrap")
async def handoff_bootstrap_route(
    request: Request,
    db: Session = Depends(get_db),
    bldcms: BLDCMSClient | None = Depends(get_bldcms_client),
):
    """Step one of arriving from BL-DCMS. Reached as POST / through nginx, never by a URL a
    user can see or type.

    WHAT ARRIVES: a form POST from BL-DCMS carrying the one-time code, cross-origin. WHAT
    LEAVES: a 303 to /, plus an HttpOnly cookie holding an opaque bootstrap id. The token is
    minted here but deliberately does NOT travel in the redirect, the URL, the fragment or any
    HTML - the SPA collects it over a same-origin call once it has loaded.

    Authorization is unchanged and happens here: login_with_handoff applies exactly the checks
    a password login applies, against this application's own tables. A refusal redirects to the
    sign-in page rather than leaking why.
    """
    if not _origin_is_allowed(request):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Unrecognized origin.")

    handoff_code = await _handoff_code_from_form(request)
    try:
        token_response = login_with_handoff(db, bldcms, handoff_code)
    except HTTPException:
        # The user should land on the Operations Dashboard's own sign-in page, not on a JSON
        # error body - they clicked a button, not an API. Nothing about the failure is encoded
        # in the URL.
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    bootstrap_id = handoff_bootstrap.create(
        employee_id="", access_token=token_response.access_token
    )
    response = RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        BOOTSTRAP_COOKIE,
        bootstrap_id,
        max_age=handoff_bootstrap.BOOTSTRAP_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        path="/",
        # secure= is deliberately not forced: this deployment is plain HTTP on an internal
        # network, and a Secure cookie would simply never be sent. Revisit with TLS.
    )
    return response


@router.post("/handoff/finalize", response_model=TokenResponse)
def handoff_finalize_route(
    bootstrap: str | None = Cookie(default=None, alias=BOOTSTRAP_COOKIE),
):
    """Step two: the freshly-loaded SPA exchanges its bootstrap cookie for the normal token.

    Same-origin, single use, and the cookie is cleared on EVERY outcome - success, replay,
    expiry and "there was never one" alike - so a spent bootstrap cannot linger in the browser.
    The response is built explicitly rather than returned as a model with an injected Response,
    because raising an HTTPException discards an injected response and the deletion would
    silently not happen on the paths that need it most.

    The token is returned in the response BODY and stored by the SPA exactly as a password
    login's token is - it never appears in a URL, a redirect or a cookie.
    """
    claimed = handoff_bootstrap.consume(bootstrap)
    if claimed is None:
        response: Response = JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": "This sign-in link is no longer valid. Please sign in."},
        )
    else:
        response = JSONResponse(
            content={"access_token": claimed.access_token, "token_type": "bearer"}
        )
    response.delete_cookie(BOOTSTRAP_COOKIE, path="/")
    return response


@router.get("/me", response_model=CurrentUser)
def me_route(current_user: User = Depends(get_current_user)):
    return to_current_user(current_user)
