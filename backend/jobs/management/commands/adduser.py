"""Tạo / đặt lại mật khẩu user.
    python manage.py adduser alice                 # hỏi mật khẩu (không hiện)
    echo "mat-khau-dai" | python manage.py adduser alice --password-stdin
    python manage.py adduser alice --staff
Mật khẩu không nhận qua tham số dòng lệnh (lộ trong lịch sử shell / danh sách process).
"""
import getpass
import sys

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Tạo user (hoặc đặt lại mật khẩu nếu đã có)"

    def add_arguments(self, parser):
        parser.add_argument("username")
        parser.add_argument("--staff", action="store_true")
        parser.add_argument("--password-stdin", action="store_true")

    def handle(self, username, staff, password_stdin, **_):
        if password_stdin:
            # PowerShell 5.1 chèn BOM (U+FEFF) khi pipe sang chương trình khác -> bỏ, nếu không mật khẩu lưu sai
            pw = sys.stdin.readline().lstrip("﻿").rstrip("\r\n")
        else:
            pw = getpass.getpass("Mật khẩu: ")
            if pw != getpass.getpass("Nhập lại: "):
                raise CommandError("hai lần nhập không khớp")
        User = get_user_model()
        user, created = User.objects.get_or_create(username=username)
        try:
            validate_password(pw, user)
        except ValidationError as e:
            if created:
                user.delete()
            raise CommandError("; ".join(e.messages))
        user.set_password(pw)
        user.is_staff = staff or user.is_staff
        user.save()
        self.stdout.write(f"{'đã tạo' if created else 'đã đặt lại mật khẩu cho'} user {username}")
