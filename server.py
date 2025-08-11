import os
import sqlite3
from flask import Flask, send_from_directory, request, jsonify
from datetime import datetime, date, timedelta
import requests
import hashlib
import hmac
import urllib.parse

app = Flask(__name__)
WEBAPP_DIR = "webapp"
DB_PATH = "chillivili.db"

# --- Вспомогательные функции ---
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

ADMIN_BOT_TOKEN = "API"
ADMIN_USER_ID = TGID

def notify_admin(text):
    url = f"https://api.telegram.org/bot{ADMIN_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": ADMIN_USER_ID, "text": text}
    try:
        response = requests.post(url, data=payload, timeout=5)
        if response.status_code != 200:
            print(f"[admin notify error] Status: {response.status_code}, Response: {response.text}")
    except Exception as e:
        print(f"[admin notify error] {e}")

MAIN_BOT_TOKEN = "API"
def notify_user(user_id, text):
    url = f"https://api.telegram.org/bot{MAIN_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": user_id, "text": text}
    try:
        response = requests.post(url, data=payload, timeout=5)
        if response.status_code != 200:
            print(f"[user notify error] Status: {response.status_code}, Response: {response.text}")
    except Exception as e:
        print(f"[user notify error] {e}")

# --- Маршруты для статики ---
@app.route('/')
def index():
    return send_from_directory(WEBAPP_DIR, 'index.html')

@app.route('/<path:filename>')
def serve_static(filename):
    return send_from_directory(WEBAPP_DIR, filename)

@app.route('/auth')
def telegram_auth():
    """Обработка авторизации через Telegram Login Widget"""
    try:
        # Получаем данные от Telegram
        telegram_data = request.args.to_dict()
        user_id = telegram_data.get('id')
        first_name = telegram_data.get('first_name')
        username = telegram_data.get('username')
        
        if user_id:
            # Сохраняем user_id в сессии или localStorage
            return f"""
            <script>
                localStorage.setItem('telegram_user_id', '{user_id}');
                localStorage.setItem('telegram_first_name', '{first_name}');
                window.location.href = '/';
            </script>
            """
        else:
            return "Ошибка авторизации", 400
    except Exception as e:
        return f"Ошибка: {str(e)}", 500

# --- API: Получение доступного времени ---
@app.route('/api/available-times/<date_str>')
def get_available_times(date_str):
    try:
        duration = int(request.args.get('duration', 1))
        conn = get_db()
        cur = conn.cursor()
        
        # Получаем все временные слоты
        cur.execute("SELECT time FROM time_slots ORDER BY time")
        all_times = [row[0] for row in cur.fetchall()]
        
        # Получаем забронированные времена с длительностью для выбранной даты
        cur.execute("""
            SELECT time, duration FROM bookings 
            WHERE date = ? AND status != 'cancelled'
        """, (date_str,))
        existing_bookings = cur.fetchall()
        
        # Создаем множество заблокированных временных слотов
        blocked_times = set()
        
        for booking_time, booking_duration in existing_bookings:
            # Вычисляем все временные слоты, которые заблокированы этим бронированием
            start_time = datetime.strptime(booking_time, '%H:%M')
            for i in range(booking_duration):
                blocked_time = start_time + timedelta(hours=i)
                blocked_times.add(blocked_time.strftime('%H:%M'))
        
        # Формируем список доступных стартовых слотов
        available_times = []
        for start_time in all_times:
            # Проверяем, можно ли начать бронирование в это время
            start_datetime = datetime.strptime(start_time, '%H:%M')
            can_book = True
            
            for i in range(duration):
                check_time = start_datetime + timedelta(hours=i)
                check_time_str = check_time.strftime('%H:%M')
                if check_time_str in blocked_times:
                    can_book = False
                    break
            
            if can_book:
                available_times.append(start_time)
        
        conn.close()
        return jsonify({"success": True, "times": available_times})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# --- API: Создание бронирования ---
@app.route('/api/book', methods=['POST'])
def create_booking():
    try:
        data = request.get_json()
        required_fields = ['date', 'time', 'guests', 'duration', 'name', 'phone']
        for field in required_fields:
            if field not in data:
                return jsonify({"success": False, "error": f"Missing required field: {field}"}), 400
        
        name = data['name'].strip()
        phone = data['phone'].strip()
        if not name or not phone:
            return jsonify({"success": False, "error": "Имя и номер телефона обязательны для бронирования."}), 400
        
        # Создаем анонимного пользователя или находим существующего по телефону
        conn = get_db()
        cur = conn.cursor()
        
        # Ищем пользователя по телефону
        cur.execute("SELECT id FROM users WHERE phone = ?", (phone,))
        user = cur.fetchone()
        
        if user:
            user_id = user[0]
            # Обновляем имя если оно изменилось
            cur.execute("UPDATE users SET name = ? WHERE id = ?", (name, user_id))
        else:
            # Создаем нового пользователя
            cur.execute(
                "INSERT INTO users (name, phone, created_at) VALUES (?, ?, ?)",
                (name, phone, datetime.now().isoformat())
            )
            user_id = cur.lastrowid
        
        total_price = data['guests'] * data['duration'] * 500
        
        # Проверяем доступность времени
        start_time = data['time']
        duration = data['duration']
        
        # Получаем все занятые временные слоты для этой даты
        cur.execute("""
            SELECT time, duration FROM bookings 
            WHERE date = ? AND status != 'cancelled'
        """, (data['date'],))
        existing_bookings = cur.fetchall()
        
        # Создаем множество заблокированных временных слотов
        blocked_times = set()
        
        for existing_time, existing_duration in existing_bookings:
            # Вычисляем все временные слоты, которые заблокированы этим бронированием
            start_time_existing = datetime.strptime(existing_time, '%H:%M')
            for i in range(existing_duration):
                blocked_time = start_time_existing + timedelta(hours=i)
                blocked_times.add(blocked_time.strftime('%H:%M'))
        
        # Проверяем, не пересекается ли новое бронирование с существующими
        booking_start = datetime.strptime(start_time, '%H:%M')
        for i in range(duration):
            check_time = booking_start + timedelta(hours=i)
            check_time_str = check_time.strftime('%H:%M')
            if check_time_str in blocked_times:
                return jsonify({"success": False, "error": f"Время {start_time} уже занято"}), 400
        
        # Создаем бронирование
        cur.execute(
            "INSERT INTO bookings (user_id, date, time, guests, duration, total_price, status, created_at) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)",
            (user_id, data['date'], data['time'], data['guests'], data['duration'], total_price, datetime.now().isoformat())
        )
        conn.commit()
        booking_id = cur.lastrowid
        conn.close()
        
        # Уведомляем админа
        notify_admin(f"Новая заявка!\nИмя: {name}\nТелефон: {phone}\nДата: {data['date']}\nВремя: {data['time']}\nГости: {data['guests']}\nДлительность: {data['duration']} ч.\nID брони: {booking_id}")
        
        return jsonify({"success": True, "booking_id": booking_id, "total_price": total_price, "message": "Бронирование успешно создано!"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# --- API: Получение бронирований пользователя ---
@app.route('/api/bookings', methods=['GET'])
def get_user_bookings():
    try:
        conn = get_db()
        cur = conn.cursor()
        
        # Получаем все бронирования с информацией о пользователях
        cur.execute("""
            SELECT b.id, b.date, b.time, b.guests, b.duration, b.total_price, b.status, 
                   u.name, u.phone
            FROM bookings b
            JOIN users u ON b.user_id = u.id
            ORDER BY b.date DESC, b.time DESC
        """)
        
        bookings = []
        for row in cur.fetchall():
            bookings.append({
                'id': row[0],
                'date': row[1],
                'time': row[2],
                'guests': row[3],
                'duration': row[4],
                'total_price': row[5],
                'status': row[6],
                'name': row[7],
                'phone': row[8]
            })
        
        conn.close()
        return jsonify({"success": True, "bookings": bookings})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# --- API: Отмена бронирования ---
@app.route('/api/bookings/<int:booking_id>/cancel', methods=['POST'])
def cancel_booking(booking_id):
    try:
        conn = get_db()
        cur = conn.cursor()
        
        # Получаем информацию о бронировании перед отменой
        cur.execute("SELECT u.telegram_id, b.date, b.time FROM bookings b JOIN users u ON b.user_id = u.id WHERE b.id = ?", (booking_id,))
        booking_info = cur.fetchone()
        
        cur.execute("UPDATE bookings SET status = 'cancelled' WHERE id = ? AND status != 'cancelled'", (booking_id,))
        conn.commit()
        affected = cur.rowcount
        conn.close()
        
        if affected > 0:
            # Уведомляем админа
            notify_admin(f"Заявка отменена!\nID: {booking_id}")
            
            # Уведомляем пользователя если есть telegram_id
            if booking_info and booking_info[0]:
                notify_user(booking_info[0], f"Ваша бронь отменена!\nДата: {booking_info[1]}\nВремя: {booking_info[2]}")
            
            return jsonify({"success": True, "message": "Бронирование успешно отменено!"})
        else:
            return jsonify({"success": False, "error": "Бронирование не найдено или уже отменено"}), 404
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# --- API: Получение всех бронирований для админа ---
@app.route('/api/admin/bookings')
def admin_get_all_bookings():
    try:
        date_filter = request.args.get('date')
        today = datetime.now().date().isoformat()
        conn = get_db()
        cur = conn.cursor()
        base_query = '''
            SELECT b.id, b.user_id, u.name, u.phone, b.date, b.time, b.guests, b.duration, b.total_price, b.status, b.created_at
            FROM bookings b
            LEFT JOIN users u ON b.user_id = u.id
        '''
        params = []
        if date_filter:
            base_query += ' WHERE b.date = ?'
            params.append(date_filter)
        else:
            base_query += ' WHERE b.date >= ?'
            params.append(today)
        base_query += ' ORDER BY b.date DESC, b.time DESC'
        cur.execute(base_query, params)
        bookings = []
        for row in cur.fetchall():
            bookings.append({
                "id": row[0],
                "user_id": row[1],
                "name": row[2],
                "phone": row[3],
                "date": row[4],
                "time": row[5],
                "guests": row[6],
                "duration": row[7],
                "total_price": row[8],
                "status": row[9],
                "created_at": row[10]
            })
        conn.close()
        return jsonify({"success": True, "bookings": bookings})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/admin/bookings/<int:booking_id>/cancel', methods=['POST'])
def admin_cancel_booking(booking_id):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("UPDATE bookings SET status = 'cancelled' WHERE id = ? AND status != 'cancelled'", (booking_id,))
        conn.commit()
        affected = cur.rowcount
        conn.close()
        if affected > 0:
            return jsonify({"success": True, "message": "Бронирование успешно отменено!"})
        else:
            return jsonify({"success": False, "error": "Бронирование не найдено или уже отменено"}), 404
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/admin/bookings/<int:booking_id>/edit', methods=['POST'])
def admin_edit_booking(booking_id):
    try:
        data = request.get_json()
        allowed_fields = ['date', 'time', 'guests', 'duration', 'status', 'total_price', 'notes']
        set_clauses = []
        values = []
        for field in allowed_fields:
            if field in data:
                set_clauses.append(f"{field} = ?")
                values.append(data[field])
        if not set_clauses:
            return jsonify({"success": False, "error": "Нет данных для обновления."}), 400
        values.append(booking_id)
        conn = get_db()
        cur = conn.cursor()
        cur.execute(f"UPDATE bookings SET {', '.join(set_clauses)} WHERE id = ?", values)
        conn.commit()
        affected = cur.rowcount
        conn.close()
        if affected > 0:
            return jsonify({"success": True, "message": "Бронирование успешно обновлено!"})
        else:
            return jsonify({"success": False, "error": "Бронирование не найдено или не изменено"}), 404
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/admin/bookings/<int:booking_id>/delete', methods=['POST'])
def admin_delete_booking(booking_id):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("DELETE FROM bookings WHERE id = ?", (booking_id,))
        conn.commit()
        affected = cur.rowcount
        conn.close()
        if affected > 0:
            return jsonify({"success": True, "message": "Бронирование полностью удалено!"})
        else:
            return jsonify({"success": False, "error": "Бронирование не найдено"}), 404
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# --- Проверка здоровья ---
@app.route('/health')
def health_check():
    return jsonify({"status": "healthy", "timestamp": datetime.now().isoformat(), "service": "ChilliVili WebApp API"})

if __name__ == '__main__':
    print("🚀 Запуск веб-сервера для Telegram Mini App...")
    print("📱 Приложение будет доступно по адресу:    https://628164fc148f.ngrok-free.app")
    print("🌐 Для продакшена загрузите файлы на хостинг и обновите WEBAPP_URL в bot.py")
    app.run(host='0.0.0.0', port=5000, debug=True) 
