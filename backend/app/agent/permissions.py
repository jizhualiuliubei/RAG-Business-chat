"""Agent 审批授权，不扩展平台管理员的跨企业业务权限。"""

from sqlalchemy import select

from app.models.enterprise import Enterprise
from app.models.user import User


def can_review_enterprise(db, user, enterprise_id):
    enterprise = db.get(Enterprise, enterprise_id)
    return bool(user and enterprise and enterprise.status == "active" and user.status == "active"
                and user.enterprise_id == enterprise_id
                and (user.role == "enterprise_admin"
                     or (enterprise.code == "system" and user.role in {"system_admin", "admin"})))


def eligible_reviewers(db, enterprise_id, applicant_id):
    users = db.execute(select(User).where(User.enterprise_id == enterprise_id,
        User.status == "active", User.id != applicant_id)).scalars()
    return [user.id for user in users if can_review_enterprise(db, user, enterprise_id)]
