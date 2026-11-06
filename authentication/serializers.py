from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers
from django.contrib.auth import get_user_model

User = get_user_model()


class AuthSerializer(serializers.Serializer):
    phoneNumber = serializers.CharField(max_length=20, trim_whitespace=False, required=False)
    email = serializers.CharField(max_length=120, trim_whitespace=False, required=False)

    def validate(self, attrs):
        phone = attrs.get("phoneNumber")
        email = attrs.get("email")

        # Determine identifier and mode
        if phone and not email:
            # Customer: phone-only auth
            identifier = phone.strip()
            # Check if user exists by using phone as email
            try:
                user = User.objects.get(email=identifier)
                attrs["user"] = user
                attrs["mode"] = "login"
            except User.DoesNotExist:
                # New user → sign up mode, phone becomes email
                attrs["email"] = identifier
                attrs["mode"] = "signup"
        elif email and not phone:
            # Store: email/password auth
            identifier = email.strip()
            try:
                user = User.objects.get(email=identifier)
                attrs["user"] = user
                attrs["mode"] = "login"
            except User.DoesNotExist:
                # New user → sign up mode
                attrs["email"] = identifier
                attrs["mode"] = "signup"
        else:
            raise serializers.ValidationError("Provide either phoneNumber or email, not both.")

        return attrs


class RegisterSerializer(serializers.Serializer):
    phoneNumber = serializers.CharField(max_length=20, trim_whitespace=False)
    fullName = serializers.CharField(min_length=2, max_length=120, required=False, allow_blank=True)

    def validate(self, attrs):
        phone = attrs.get("phoneNumber").strip()
        username = phone

        # Check if user already exists
        if User.objects.filter(username=username).exists():
            raise serializers.ValidationError("This phone number is already registered.")

        attrs["username"] = username
        attrs["email"] = phone.strip() + "@mechanic.local"  # internal email
        attrs["mode"] = "signup"

        return attrs


class LoginSerializer(serializers.Serializer):
    phoneNumber = serializers.CharField(max_length=20, trim_whitespace=False)

    def validate(self, attrs):
        phone = attrs.get("phoneNumber").strip()
        username = phone

        # Check if user exists
        try:
            user = User.objects.get(username=username)
            attrs["user"] = user
            attrs["mode"] = "login"
        except User.DoesNotExist:
            raise serializers.ValidationError("Phone number not registered. Please sign up first.")

        return attrs


class RefreshSerializer(serializers.Serializer):
    refreshToken = serializers.CharField(max_length=256)


class ChangePasswordSerializer(serializers.Serializer):
    currentPassword = serializers.CharField(max_length=128, trim_whitespace=False, required=True)
    newPassword = serializers.CharField(max_length=128, trim_whitespace=False, required=True)
    confirmPassword = serializers.CharField(max_length=128, trim_whitespace=False, required=True)

    def validate_currentPassword(self, value):
        user = self.context.get("user")
        if user is None:
            raise serializers.ValidationError("Authentication required.")
        if not user.check_password(value):
            raise serializers.ValidationError("Current password is incorrect.")
        return value

    def validate_newPassword(self, value):
        value = (value or "").strip()
        if len(value) < 8:
            raise serializers.ValidationError("Password must be at least 8 characters.")
        if not any(c.isdigit() for c in value):
            raise serializers.ValidationError("Password must contain at least one number.")
        if not any(c in "!@#$%^&*()_+-=[]{}|;:,.<>?" for c in value):
            raise serializers.ValidationError("Password must contain at least one symbol.")
        return value

    def validate(self, attrs):
        new_password = attrs.get("newPassword")
        confirm_password = attrs.get("confirmPassword")
        if new_password != confirm_password:
            raise serializers.ValidationError("New password and confirmation do not match.")
        if new_password == attrs.get("currentPassword"):
            raise serializers.ValidationError("New password must be different from current password.")
        return attrs


class MeUpdateSerializer(serializers.Serializer):
    fullName = serializers.CharField(max_length=120, required=False, allow_blank=True)
    city = serializers.CharField(max_length=50, required=False, allow_blank=True)
    street = serializers.CharField(max_length=200, required=False, allow_blank=True)
    phone = serializers.CharField(max_length=20, required=False, allow_blank=True)
    email = serializers.EmailField(required=False, allow_blank=True)

    def validate_fullName(self, value):
        value = (value or "").strip()
        if len(value) < 2:
            raise serializers.ValidationError("Full name must be at least 2 characters.")
        return value

    def validate_street(self, value):
        return (value or "").strip()

    def validate_phone(self, value):
        value = (value or "").strip()
        if not value or not value.isdigit() or not 7 <= len(value) <= 15:
            raise serializers.ValidationError("Enter a valid phone number (7-15 digits).")
        return value

    def validate_email(self, value):
        from django.core.validators import EmailValidator
        value = (value or "").strip()
        if value:
            EmailValidator()(value)
        return value

    def validate_city(self, value):
        value = (value or "").strip().lower()
        if not value:
            return ""
        from customer.delivery import KsaDelivery
        if not KsaDelivery.is_valid(value):
            raise serializers.ValidationError("Choose a supported KSA city.")
        return value

    def validate(self, attrs):
        user = self.context.get("user")
        if user is None:
            raise serializers.ValidationError("Authentication required.")
        uses_phone = user.email.isdigit()
        if uses_phone:
            if "email" in attrs and not attrs.get("phone"):
                raise serializers.ValidationError("Phone accounts update their phone number, not an email.")
        else:
            if "phone" in attrs:
                raise serializers.ValidationError("Email accounts update their email address, not a phone number.")
        return attrs