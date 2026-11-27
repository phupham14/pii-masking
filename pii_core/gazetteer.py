"""Từ điển dùng chung: địa danh, họ người Việt, danh xưng."""

VN_PLACES = [
    "Hanoi", "Ha Noi", "Ho Chi Minh", "Saigon", "Sai Gon", "Da Nang", "Hai Phong", "Can Tho",
    "Hue", "Nha Trang", "Vung Tau", "Da Lat", "Dalat", "Quy Nhon", "Bien Hoa", "Thu Duc",
    "Binh Duong", "Dong Nai", "Bac Ninh", "Hai Duong", "Quang Ninh", "Ha Long", "Nghe An",
    "Thanh Hoa", "Khanh Hoa", "Lam Dong", "Long An", "Viet Nam", "Vietnam",
    # có dấu
    "Hà Nội", "Hồ Chí Minh", "Sài Gòn", "Đà Nẵng", "Hải Phòng", "Cần Thơ", "Huế", "Nha Trang",
    "Vũng Tàu", "Đà Lạt", "Quy Nhơn", "Biên Hòa", "Thủ Đức", "Bình Dương", "Đồng Nai", "Bắc Ninh",
    "Hải Dương", "Quảng Ninh", "Hạ Long", "Nghệ An", "Thanh Hóa", "Khánh Hòa", "Lâm Đồng", "Việt Nam",
    "Bà Rịa", "Bà Rịa - Vũng Tàu", "Long An", "Tây Ninh", "Bình Phước", "Bình Thuận", "Ninh Thuận",
    "Phú Yên", "Bình Định", "Quảng Ngãi", "Quảng Nam", "Thừa Thiên Huế", "Quảng Trị", "Quảng Bình",
    "Hà Tĩnh", "Ninh Bình", "Nam Định", "Thái Bình", "Hưng Yên", "Hà Nam", "Vĩnh Phúc", "Phú Thọ",
    "Thái Nguyên", "Bắc Giang", "Lạng Sơn", "Cao Bằng", "Hà Giang", "Lào Cai", "Yên Bái", "Tuyên Quang",
    "Bắc Kạn", "Sơn La", "Điện Biên", "Lai Châu", "Hòa Bình", "Kon Tum", "Gia Lai", "Đắk Lắk",
    "Đắk Nông", "Tiền Giang", "Bến Tre", "Trà Vinh", "Vĩnh Long", "Đồng Tháp", "An Giang", "Kiên Giang",
    "Hậu Giang", "Sóc Trăng", "Bạc Liêu", "Cà Mau", "Campuchia", "Phnom Penh",
    # Bắt đầu bằng một họ phổ biến -> luật họ tên hay nhận nhầm thành người (rà trên văn bản ngân hàng 2026-11)
    "Châu Âu", "Châu Á", "Châu Mỹ", "Châu Phi", "Châu Úc", "Châu Đại Dương",
    "Từ Liêm", "Nam Từ Liêm", "Bắc Từ Liêm", "Hà Đông", "Lê Chân", "Ngô Quyền",
]

VN_SURNAMES = {
    "Nguyễn", "Trần", "Lê", "Phạm", "Hoàng", "Huỳnh", "Phan", "Vũ", "Võ", "Đặng", "Bùi", "Đỗ", "Hồ",
    "Ngô", "Dương", "Lý", "Đinh", "Lã", "Hà", "Trịnh", "Đoàn", "Lâm", "Mai", "Cao", "Tô", "Kim", "Lưu",
    "Trương", "Tạ", "Châu", "Quách", "Thái", "Tăng", "Phùng", "Đào", "Kiều", "La", "Từ", "Chu",
    "Triệu", "Tôn", "Âu", "Lương", "Mạc", "Doãn", "Vương", "Diệp", "Hứa", "Lục", "Khúc", "Ninh",
}
HONORIFICS = {"ông", "bà", "anh", "chị", "mr.", "ms.", "mrs."}
# Từ đứng ngay trước => đây là địa chỉ / tên tổ chức, KHÔNG phải tên người
NON_PERSON_PREV = {
    "đường", "phố", "phường", "quận", "huyện", "xã", "tỉnh", "tp", "tp.", "số", "nhánh",
    "án", "ty", "tnhh", "phần", "hàng", "tư", "cầu", "ngõ", "ngách", "hẻm", "ấp", "thôn", "khu",
}
