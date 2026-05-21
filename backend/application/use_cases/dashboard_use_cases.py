from typing import Any, Dict, List, Optional

from domain.repositories.camera_repository import CameraRepository
from domain.repositories.user_repository import UserRepository
from database.sqlite_db import (
    get_total_vehicle_count,
    get_illegal_parking_violations,
    get_illegal_parking_count,
    get_congestion_count,
    global_search,
    get_daily_vehicle_stats,
    get_latest_violations,
    get_vehicle_type_distribution,
    get_unread_notifications,
    mark_notification_as_read,
)


class DashboardUseCases:
    def __init__(self, user_repo: UserRepository, camera_repo: CameraRepository):
        self.user_repo = user_repo
        self.camera_repo = camera_repo

    def get_dashboard_stats(self, period: str = "all", camera_ids: Optional[List[int]] = None) -> Dict[str, Any]:
        from datetime import datetime, timedelta
        
        start_date = None
        end_date = None
        chart_limit = 30
        
        if period == "today":
            start_date = datetime.now().strftime("%Y-%m-%d")
            end_date = start_date
            chart_limit = 1
        elif period == "7days":
            start_date = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
            end_date = datetime.now().strftime("%Y-%m-%d")
            chart_limit = 7
        elif period == "30days":
            start_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
            end_date = datetime.now().strftime("%Y-%m-%d")
            chart_limit = 30
        elif period == "all":
            chart_limit = 30 # Mặc định biểu đồ hiển thị 30 ngày gần nhất khi xem tất cả
            
        users = self.user_repo.list_all()
        cameras = self.camera_repo.list_all()
        
        # Nếu có camera_ids, lọc chỉ hiển thị dữ liệu cho các camera được phép
        if camera_ids is not None:
            cameras = [c for c in cameras if c.id in camera_ids]
        
        feature_counts = self.camera_repo.get_feature_counts()
        recent_cameras = self.camera_repo.get_recent(limit=6)
        
        # Lọc recent_cameras và feature_counts theo camera_ids nếu có
        if camera_ids is not None:
            recent_cameras = [c for c in recent_cameras if c.id in camera_ids]
        
        # Lấy thống kê giao thông theo khoảng thời gian đồng nhất, lọc theo camera_ids
        total_vehicles = get_total_vehicle_count(start_date, end_date, camera_ids=camera_ids)
        parking_violation_count = get_illegal_parking_count(start_date, end_date, camera_ids=camera_ids)
        congestion_count = get_congestion_count(start_date, end_date, camera_ids=camera_ids)
        
        # Biểu đồ và Phân loại cũng phải lọc theo cùng khoảng thời gian
        daily_stats = get_daily_vehicle_stats(start_date, end_date, limit=chart_limit, camera_ids=camera_ids)
        latest_violations = get_latest_violations(limit=5, camera_ids=camera_ids)
        vehicle_distribution = get_vehicle_type_distribution(start_date, end_date, camera_ids=camera_ids)

        return {
            "user_count": len(users),
            "camera_count": len(cameras),
            "active_cameras": sum(1 for c in cameras if c.is_active),
            "congestion_enabled": feature_counts.get("congestion", 0),
            "illegal_parking_enabled": feature_counts.get("illegal_parking", 0),
            "license_plate_enabled": feature_counts.get("license_plate", 0),
            "recent_cameras": [cam.to_dict() for cam in recent_cameras],
            "total_vehicles": total_vehicles,
            "parking_violation_count": parking_violation_count,
            "congestion_count": congestion_count,
            "period": period,
            "daily_stats": daily_stats,
            "latest_violations": latest_violations,
            "vehicle_distribution": vehicle_distribution
        }

    def get_settings(self) -> Dict[str, Any]:
        return {}

    def update_settings(self, settings: Dict[str, Any]) -> None:
        pass

    def search(self, query: str) -> Dict[str, Any]:
        return global_search(query)

    def get_notifications(self, limit: int = 10) -> Dict[str, Any]:
        return get_unread_notifications(limit)

    def mark_notification_read(self, notif_type: str, record_id: int) -> bool:
        return mark_notification_as_read(notif_type, record_id)