# handlers/__init__.py
from handlers.start import router as start_router
from handlers.onboarding import router as onboarding_router
from handlers.registration import router as registration_router
from handlers.post import router as post_router
from handlers.subscriptions import router as subscriptions_router
from handlers.my_posts import router as my_posts_router
from handlers.profile import router as profile_router
from handlers.rating import router as rating_router
from handlers.callbacks import router as callbacks_router

__all__ = [
    "start_router",
    "onboarding_router",
    "registration_router",
    "post_router",
    "subscriptions_router",
    "my_posts_router",
    "profile_router",
    "rating_router",
    "callbacks_router"
]
