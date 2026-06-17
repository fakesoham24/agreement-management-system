from fastapi import APIRouter, Depends
from backend.auth import get_current_user
from backend.database import get_db

router = APIRouter(prefix="/api/notifications", tags=["Notifications"])


@router.get("/")
def get_notifications(current_user: dict = Depends(get_current_user), db=Depends(get_db)):
    cursor = db.cursor()
    notifications = cursor.execute(
        """SELECT n.*, aa.company_name 
           FROM notifications n 
           LEFT JOIN agreement_analysis aa ON n.agreement_id = aa.agreement_id
           WHERE n.user_id = ? 
           ORDER BY n.created_at DESC LIMIT 50""",
        (current_user["id"],)
    ).fetchall()
    return {"notifications": [dict(n) for n in notifications]}


@router.put("/{notification_id}/read")
def mark_read(notification_id: int, current_user: dict = Depends(get_current_user), db=Depends(get_db)):
    cursor = db.cursor()
    notification = cursor.execute(
        "SELECT * FROM notifications WHERE id = ? AND user_id = ?",
        (notification_id, current_user["id"])
    ).fetchone()
    if not notification:
        return {"message": "Not found"}

    cursor.execute("UPDATE notifications SET is_read = 1 WHERE id = ?", (notification_id,))
    db.commit()
    return {"message": "Marked as read"}


@router.put("/read-all")
def mark_all_read(current_user: dict = Depends(get_current_user), db=Depends(get_db)):
    cursor = db.cursor()
    cursor.execute("UPDATE notifications SET is_read = 1 WHERE user_id = ?", (current_user["id"],))
    db.commit()
    return {"message": "All notifications marked as read"}


@router.delete("/{notification_id}")
def delete_notification(notification_id: int, current_user: dict = Depends(get_current_user), db=Depends(get_db)):
    """Delete a single notification."""
    cursor = db.cursor()
    notification = cursor.execute(
        "SELECT * FROM notifications WHERE id = ? AND user_id = ?",
        (notification_id, current_user["id"])
    ).fetchone()
    if not notification:
        return {"message": "Not found"}

    cursor.execute("DELETE FROM notifications WHERE id = ?", (notification_id,))
    db.commit()
    return {"message": "Notification deleted"}


@router.delete("/")
def delete_all_notifications(current_user: dict = Depends(get_current_user), db=Depends(get_db)):
    """Delete all notifications for the current user."""
    cursor = db.cursor()
    cursor.execute("DELETE FROM notifications WHERE user_id = ?", (current_user["id"],))
    db.commit()
    return {"message": "All notifications deleted"}

