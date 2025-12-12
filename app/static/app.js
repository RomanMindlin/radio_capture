async function login(event) {
    event.preventDefault();
    const formData = new FormData(event.target);
    const response = await fetch('/api/auth/login', {
        method: 'POST',
        body: formData
    });
    if (response.ok) {
        window.location.href = '/dashboard';
    } else {
        alert('Login failed');
    }
}

async function logout() {
    await fetch('/api/auth/logout', { method: 'POST' });
    window.location.href = '/login';
}

// Polling for status updates?
// For now, page reload or basic fetch
