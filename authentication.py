"""Verify Cognito identity before the agent runs. Never a model tool."""
from functools import lru_cache
import os

from fastapi import HTTPException
import jwt
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientConnectionError


@lru_cache(maxsize=1)
def signing_keys(issuer):
    return PyJWKClient(issuer + '/.well-known/jwks.json', timeout=5)


def verify_login(access_token):
    if not access_token:
        raise HTTPException(401, 'Sign in with Cognito first.')
    region = os.getenv('COGNITO_REGION')
    pool = os.getenv('COGNITO_USER_POOL_ID')
    client = os.getenv('COGNITO_CLIENT_ID')
    if not all([region, pool, client]):
        raise HTTPException(503, 'Cognito verification is not configured on this runtime.')
    issuer = f'https://cognito-idp.{region}.amazonaws.com/{pool}'
    try:
        key = signing_keys(issuer).get_signing_key_from_jwt(access_token)
        claims = jwt.decode(access_token, key.key, algorithms=['RS256'], issuer=issuer,
            options={'verify_aud': False,
                     'require': ['exp', 'iat', 'sub', 'iss', 'client_id', 'token_use']})
        # Cognito access tokens identify their app using client_id, not ID-token aud.
        if claims['token_use'] != 'access' or claims['client_id'] != client:
            raise HTTPException(401, 'Token is not an access token for this application.')
        return claims['sub']
    except PyJWKClientConnectionError:
        raise HTTPException(503, 'Cannot reach Cognito signing keys. Try again shortly.') from None
    except jwt.PyJWTError:
        raise HTTPException(401, 'Login token is invalid or expired. Sign in again.') from None
