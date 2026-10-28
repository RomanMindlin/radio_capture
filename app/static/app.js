async function login(event) {
    event.preventDefault();
    const formData = new FormData(event.target);
    const response = await fetch(ROOT + '/api/auth/login', {
        method: 'POST',
        body: formData
    });
    if (response.ok) {
        window.location.href = ROOT + '/dashboard';
    } else {
        alert('Login failed');
    }
}

async function logout() {
    await fetch(ROOT + '/api/auth/logout', { method: 'POST' });
    window.location.href = ROOT + '/login';
}

// Polling for status updates?
// For now, page reload or basic fetch
