from app.core.security import create_access_token, decode_access_token, verify_password
from tests.conftest import hash_password, make_user


def test_verify_password_roundtrip():
    hashed = hash_password("correct-horse")
    assert verify_password("correct-horse", hashed) is True
    assert verify_password("wrong", hashed) is False


def test_verify_password_handles_missing_hash():
    assert verify_password("anything", None) is False


def test_jwt_roundtrip():
    token = create_access_token({"sub": "E1", "role": "Admin"})
    payload = decode_access_token(token)
    assert payload["sub"] == "E1"
    assert payload["role"] == "Admin"
    assert "exp" in payload


def test_jwt_garbage_token_rejected():
    assert decode_access_token("not-a-real-token") is None


def test_jwt_wrong_signature_rejected():
    from jose import jwt

    forged = jwt.encode({"sub": "E1"}, "a-completely-different-secret", algorithm="HS256")
    assert decode_access_token(forged) is None


def test_invalid_bearer_token_rejected_on_protected_route(client, db_session):
    resp = client.get("/api/auth/me", headers={"Authorization": "Bearer garbage"})
    assert resp.status_code == 401


def test_login_response_never_includes_password_hash(client, db_session):
    make_user(db_session, 1, "E1", "Alice Admin", "Admin", hash_password("secret123"))

    resp = client.post("/api/auth/login", json={"employee_id": "E1", "password": "secret123"})

    assert resp.status_code == 200
    body = resp.json()
    assert "password" not in body
    assert "password_hash" not in body
