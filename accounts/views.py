import random
import string
from django.contrib.auth import get_user_model, authenticate
from django.core.cache import cache
from django.core.mail import send_mail
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.authtoken.models import Token

from .serializers import (
    LoginSerializer,
    PasswordResetRequestSerializer,
    PasswordResetConfirmSerializer,
    UserSerializer,
)

User = get_user_model()


def _generate_code():
    return ''.join(random.choices(string.digits, k=6))


@api_view(['POST'])
@permission_classes([AllowAny])
def login_view(request):
    serializer = LoginSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    email    = serializer.validated_data['email']
    password = serializer.validated_data['password']

    try:
        user = User.objects.get(email=email)
    except User.DoesNotExist:
        return Response(
            {'error': 'Invalid email or password.'},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    user = authenticate(request, username=email, password=password)
    if not user:
        return Response(
            {'error': 'Invalid email or password.'},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    token, _ = Token.objects.get_or_create(user=user)

    return Response({
        'token': token.key,
        'role':  user.role,
        'email': user.email,
    })


@api_view(['POST'])
@permission_classes([AllowAny])
def password_reset_request(request):
    serializer = PasswordResetRequestSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    email = serializer.validated_data['email']

    try:
        user = User.objects.get(email=email)
    except User.DoesNotExist:
        return Response({'message': 'If this email exists, a reset code was sent.'})

    code      = _generate_code()
    cache_key = f'pwd_reset_{email}'
    cache.set(cache_key, code, timeout=600)

    html_message = f"""
<!DOCTYPE html>
<html>
<body style="margin:0;padding:0;background-color:#0f0f0f;font-family:Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background-color:#0f0f0f;padding:40px 0;">
    <tr>
      <td align="center">
        <table width="500" cellpadding="0" cellspacing="0" style="background-color:#1a1a1a;border-radius:12px;overflow:hidden;border:1px solid #333;">
          <tr>
            <td style="background-color:#ff6600;padding:30px;text-align:center;">
              <h1 style="color:#ffffff;margin:0;font-size:24px;letter-spacing:2px;">OrangNOC</h1>
              <p style="color:#ffe0cc;margin:5px 0 0;font-size:13px;">Orange Tunisia — Network Operating Center</p>
            </td>
          </tr>
          <tr>
            <td style="padding:40px 30px;text-align:center;">
              <p style="color:#aaaaaa;font-size:15px;margin:0 0 20px;">You requested a password reset. Use the code below:</p>
              <div style="background-color:#0f0f0f;border:2px solid #ff6600;border-radius:8px;padding:20px;margin:20px 0;display:inline-block;">
                <span style="color:#ff6600;font-size:36px;font-weight:bold;letter-spacing:8px;">{code}</span>
              </div>
              <p style="color:#aaaaaa;font-size:13px;margin:20px 0 0;">⏱ Valid for <strong style="color:#ffffff;">10 minutes</strong></p>
              <p style="color:#666666;font-size:12px;margin:10px 0 0;">If you didn't request this, ignore this email.</p>
            </td>
          </tr>
          <tr>
            <td style="background-color:#111111;padding:20px;text-align:center;border-top:1px solid #333;">
              <p style="color:#555555;font-size:11px;margin:0;">©Orange Tunisia — OrangeNOC Platform</p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""

    send_mail(
        subject='DRS010 — Password Reset Code',
        message=f'Your password reset code is: {code}\n\nValid for 10 minutes.',
        from_email='rrrooouuuaaa401@gmail.com',
        recipient_list=[email],
        fail_silently=False,
        html_message=html_message,
    )

    return Response({'message': 'If this email exists, a reset code was sent.'})


@api_view(['POST'])
@permission_classes([AllowAny])
def password_reset_confirm(request):
    serializer = PasswordResetConfirmSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    email        = serializer.validated_data['email']
    code         = serializer.validated_data['code']
    new_password = serializer.validated_data['new_password']

    cache_key   = f'pwd_reset_{email}'
    stored_code = cache.get(cache_key)

    if not stored_code or stored_code != code:
        return Response(
            {'error': 'Invalid or expired reset code.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        user = User.objects.get(email=email)
    except User.DoesNotExist:
        return Response(
            {'error': 'User not found.'},
            status=status.HTTP_404_NOT_FOUND,
        )

    user.set_password(new_password)
    user.save()
    cache.delete(cache_key)

    return Response({'message': 'Password reset successful. You can now log in.'})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def logout_view(request):
    request.user.auth_token.delete()
    return Response({'message': 'Logged out successfully.'})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def register_view(request):
    if request.user.role != 'admin':
        return Response(
            {'error': 'Only admins can create users.'},
            status=status.HTTP_403_FORBIDDEN,
        )

    email    = request.data.get('email')
    username = request.data.get('username')
    password = request.data.get('password')
    role     = request.data.get('role', 'user')

    if not all([email, username, password]):
        return Response(
            {'error': 'email, username and password are required.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if User.objects.filter(email=email).exists():
        return Response(
            {'error': 'A user with this email already exists.'},
            status=status.HTTP_409_CONFLICT,
        )

    if User.objects.filter(username=username).exists():
        return Response(
            {'error': 'A user with this username already exists.'},
            status=status.HTTP_409_CONFLICT,
        )

    if role not in ('admin', 'user'):
        return Response(
            {'error': 'role must be "admin" or "user".'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    user = User.objects.create_user(
        username=username,
        email=email,
        password=password,
        role=role,
    )

    return Response({
        'message': f'User {email} created successfully.',
        'id':      user.id,
        'email':   user.email,
        'role':    user.role,
    }, status=status.HTTP_201_CREATED)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def me_view(request):
    return Response(UserSerializer(request.user).data)
