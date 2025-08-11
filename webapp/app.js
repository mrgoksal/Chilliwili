// Инициализация Telegram Web App
let tg = window.Telegram.WebApp;

// Инициализация приложения
document.addEventListener('DOMContentLoaded', function() {
    // Только админ-панель
    showAdminPanel();
    document.getElementById('admin-date-filter-btn').addEventListener('click', function() {
        const date = document.getElementById('admin-date-input').value;
        loadAdminBookings(date);
    });
    document.getElementById('admin-date-clear-btn').addEventListener('click', function() {
        document.getElementById('admin-date-input').value = '';
        loadAdminBookings();
    });
    loadAdminBookings();
});

// Оставляем только функции для админ-панели

function showAdminPanel() {
    let panel = document.getElementById('admin-panel');
    if (!panel) {
        panel = document.createElement('div');
        panel.id = 'admin-panel';
        panel.innerHTML = `
            <h3>Панель администратора</h3>
            <div style="margin-bottom:10px;">
                <label for="admin-date-filter">Фильтр по дате: </label>
                <input type="date" id="admin-date-filter">
                <button id="admin-date-clear">Показать все будущие</button>
            </div>
            <div id="admin-bookings-list">Загрузка...</div>
        `;
        document.querySelector('.main-content').prepend(panel);
        // Навешиваем обработчики на фильтр
        document.getElementById('admin-date-filter').addEventListener('change', function() {
            loadAdminBookings(this.value);
        });
        document.getElementById('admin-date-clear').addEventListener('click', function() {
            document.getElementById('admin-date-filter').value = '';
            loadAdminBookings();
        });
    }
    loadAdminBookings();
}

function hideAdminPanel() {
    const panel = document.getElementById('admin-panel');
    if (panel) panel.remove();
}

async function loadAdminBookings(dateFilter) {
    const list = document.getElementById('admin-bookings-list');
    list.innerHTML = 'Загрузка...';
    try {
        let url = '/api/admin/bookings';
        if (dateFilter) {
            url += `?date=${dateFilter}`;
        }
        const response = await fetch(url);
        const result = await response.json();
        if (!result.success) throw new Error(result.error);
        if (result.bookings.length === 0) {
            list.innerHTML = 'Нет бронирований';
            return;
        }
        list.innerHTML = '';
        result.bookings.forEach(booking => {
            const div = document.createElement('div');
            div.className = 'admin-booking-item';
            div.innerHTML = `
                <b>ID:</b> ${booking.id} | <b>Имя:</b> ${booking.name || ''} | <b>Телефон:</b> ${booking.phone || ''}<br>
                <b>Дата:</b> ${booking.date} <b>Время:</b> ${booking.time} <b>Гости:</b> ${booking.guests} <b>Длительность:</b> ${booking.duration} ч. <b>Статус:</b> ${booking.status}<br>
                <button class="admin-cancel-btn" data-id="${booking.id}">Скрыть</button>
                <button class="admin-delete-btn" data-id="${booking.id}">Удалить</button>
                <button class="admin-edit-btn" data-id="${booking.id}">Редактировать</button>
                <hr>
            `;
            list.appendChild(div);
        });
        // Навешиваем обработчики
        document.querySelectorAll('.admin-cancel-btn').forEach(btn => {
            btn.onclick = async function() {
                if (confirm('Скрыть это бронирование (статус cancelled)?')) {
                    await fetch(`/api/admin/bookings/${btn.dataset.id}/cancel`, {method: 'POST'});
                    loadAdminBookings(dateFilter);
                }
            };
        });
        document.querySelectorAll('.admin-delete-btn').forEach(btn => {
            btn.onclick = async function() {
                if (confirm('Удалить это бронирование безвозвратно?')) {
                    await fetch(`/api/admin/bookings/${btn.dataset.id}/delete`, {method: 'POST'});
                    loadAdminBookings(dateFilter);
                }
            };
        });
        document.querySelectorAll('.admin-edit-btn').forEach(btn => {
            btn.onclick = function() {
                alert('Редактирование реализовать по желанию!');
            };
        });
    } catch (e) {
        list.innerHTML = 'Ошибка загрузки';
    }
}

// Глобальные функции для модальных окон
window.closeModal = closeModal; 