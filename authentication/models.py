import uuid

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.db import models
from django.utils import timezone


class AuthStore(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=150)
    cr_number = models.CharField(max_length=64, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "auth_stores"


class UserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("Email is required")
        email = self.normalize_email(email).lower()
        user = self.model(email=email, **extra_fields)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        if extra_fields.get("is_staff") is not True or extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_staff=True and is_superuser=True")
        return self.create_user(email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    class Role(models.TextChoices):
        CUSTOMER = ("CUSTOMER", "Customer")
        STORE_ADMIN = ("STORE_ADMIN", "Store admin")
        STORE_MANAGER = ("STORE_MANAGER", "Store manager")
        DRIVER = ("DRIVER", "Driver")

    class AuthProvider(models.TextChoices):
        LOCAL = ("LOCAL", "Local")
        GOOGLE = ("GOOGLE", "Google")
        FACEBOOK = ("FACEBOOK", "Facebook")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(unique=True)
    full_name = models.CharField(max_length=120)
    city = models.CharField(max_length=50, blank=True, default="")
    street = models.CharField(max_length=200, blank=True, default="")
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.STORE_ADMIN)
    store = models.ForeignKey(
        AuthStore,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="members",
    )
    auth_provider = models.CharField(
        max_length=20,
        choices=AuthProvider.choices,
        default=AuthProvider.LOCAL,
        db_index=True,
    )
    provider_id = models.CharField(max_length=255, null=True, blank=True)
    avatar_url = models.URLField(max_length=500, null=True, blank=True)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    USERNAME_FIELD = "email"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = UserManager()

    class Meta:
        db_table = "auth_users"
        indexes = [
            models.Index(fields=["store", "role"]),
        ]

    def __str__(self):
        return self.email


class RefreshToken(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="refresh_tokens")
    token_hash = models.CharField(max_length=64, unique=True)
    family_id = models.UUIDField(db_index=True)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "auth_refresh_tokens"

    @property
    def is_expired(self):
        return self.expires_at <= timezone.now()
