"""
认证相关 Pydantic 模型
================
登录、验证码、当前用户响应结构。
"""
from pydantic import BaseModel


class CaptchaOut(BaseModel):
    captcha_id: str
    question: str


class LoginRequest(BaseModel):
    enterprise_code: str = "system"
    username: str
    password: str
    captcha_id: str
    captcha_answer: str


class UserOut(BaseModel):
    id: int
    username: str
    display_name: str
    role: str
    status: str
    enterprise_id: int | None = None
    enterprise_name: str = ""
    enterprise_code: str = ""
    brand: dict = {}

    model_config = {"from_attributes": True}


class EnterpriseRegisterRequest(BaseModel):
    enterprise_name: str
    enterprise_code: str
    contact_name: str | None = None
    contact_email: str | None = None
    contact_phone: str | None = None
    username: str
    display_name: str
    password: str


class LoginOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut
