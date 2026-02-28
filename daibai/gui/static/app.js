/**
 * DaiBai GUI - Main Application JavaScript
 */

// ── Firebase initialisation (compat SDK, no build step required) ───────────

const firebaseConfig = {
    apiKey:            'AIzaSyBw9MKfYGp3_YpuTj2Uz0kO1ksFFzscZmU',
    authDomain:        'daibai-affb0.firebaseapp.com',
    projectId:         'daibai-affb0',
    storageBucket:     'daibai-affb0.firebasestorage.app',
    messagingSenderId: '483473257515',
    appId:             '1:483473257515:web:2a9721d068ff5bb0aba66e',
    measurementId:     'G-1THSZJTQM3',
};

firebase.initializeApp(firebaseConfig);
firebase.analytics();

// Explicitly pin session storage to localStorage so it survives page reloads.
// Must be called before onAuthStateChanged is registered.
firebase.auth().setPersistence(firebase.auth.Auth.Persistence.LOCAL)
    .catch((e) => console.error('[AUTH] setPersistence failed:', e));

const _ui = new firebaseui.auth.AuthUI(firebase.auth());
let _currentUser             = null;   // firebase.User | null — updated by onAuthStateChanged
let _pendingVerificationUser = null;   // unverified email user waiting to confirm their address
let _isPlaygroundActive      = false;  // true when "Query Chinook DB" mode is active

// ── FirebaseUI configuration ───────────────────────────────────────────────

const _uiConfig = {
    signInFlow: 'popup',
    signInOptions: [
        firebase.auth.GoogleAuthProvider.PROVIDER_ID,
        firebase.auth.GithubAuthProvider.PROVIDER_ID,
        firebase.auth.EmailAuthProvider.PROVIDER_ID,
        // AnonymousAuthProvider intentionally removed — anonymous sessions are
        // not persisted the same way and confuse the "stay logged in" flow.
    ],
    callbacks: {
        signInSuccessWithAuthResult: (authResult) => {
            const u         = authResult.user;
            const isNew     = authResult.additionalUserInfo?.isNewUser;
            const providerId = authResult.additionalUserInfo?.providerId;
            console.log('[AUTH] Sign-in success:', u.email || u.uid,
                        '| provider:', providerId,
                        '| new user:', isNew,
                        '| emailVerified:', u.emailVerified);

            // For brand-new email/password registrations, send the verification
            // email immediately so the user receives it before they can do anything.
            // onAuthStateChanged will block the app until they click the link.
            if (isNew && providerId === 'password') {
                u.sendEmailVerification()
                    .then(() => console.log('[AUTH] Verification email sent to', u.email))
                    .catch(e  => console.warn('[AUTH] sendEmailVerification failed:', e.message));
            }

            document.getElementById('authModal')?.classList.remove('active');
            return false; // must be false — prevents FirebaseUI from redirecting
        },
        uiShown: () => console.log('[AUTH] FirebaseUI widget rendered'),
    },
    tosUrl: null,
    privacyPolicyUrl: null,
};

// ── Auth API ───────────────────────────────────────────────────────────────

/** Returns true when any Firebase session is active (including anonymous). */
function isAuthenticated() { return !!_currentUser; }

/** Open the sign-in modal and render the FirebaseUI widget. */
function signIn() {
    document.getElementById('authModal')?.classList.add('active');
    _ui.start('#firebaseui-auth-container', _uiConfig);
}

/** Sign the current user out of Firebase. */
async function signOut() {
    _pendingVerificationUser = null;
    await firebase.auth().signOut();
    // onAuthStateChanged fires next and resets _currentUser + UI
}

// ── Email verification ─────────────────────────────────────────────────────

/**
 * Returns true when the user MUST verify their email before accessing the app.
 * Only email/password accounts require this — OAuth providers (Google, GitHub)
 * already guarantee a verified email so their users always have emailVerified=true.
 */
function _requiresEmailVerification(user) {
    const isEmailPassword = user.providerData.some(p => p.providerId === 'password');
    return isEmailPassword && !user.emailVerified;
}

/** Show the blocking verification modal and populate the email address. */
function _showVerificationModal(email) {
    const modal = document.getElementById('verificationModal');
    const emailEl = document.getElementById('verificationEmail');
    const status = document.getElementById('verificationStatus');
    if (emailEl) emailEl.textContent = email || '';
    if (status)  { status.textContent = ''; status.className = 'verification-status'; }
    modal?.classList.add('active');
    // Re-run Lucide so icons inside the modal are rendered.
    if (typeof lucide !== 'undefined') lucide.createIcons();
}

/** Hide the verification modal. */
function _hideVerificationModal() {
    document.getElementById('verificationModal')?.classList.remove('active');
}

/** "Resend Verification Email" button handler. */
async function resendVerificationEmail() {
    const btn    = document.getElementById('resendVerificationBtn');
    const status = document.getElementById('verificationStatus');
    const user   = _pendingVerificationUser || firebase.auth().currentUser;
    if (!user) return;
    if (btn) btn.disabled = true;
    if (status) { status.textContent = 'Sending…'; status.className = 'verification-status loading'; }
    try {
        await user.sendEmailVerification();
        if (status) { status.textContent = 'Sent! Check your inbox (and spam folder).'; status.className = 'verification-status success'; }
    } catch (e) {
        console.error('[AUTH] resendVerificationEmail:', e);
        if (status) { status.textContent = `Could not send: ${e.message}`; status.className = 'verification-status error'; }
    } finally {
        if (btn) { setTimeout(() => { btn.disabled = false; }, 5000); } // rate-limit UI
    }
}

/**
 * "I've Verified My Email" button handler.
 * Reloads the Firebase user to pick up the latest emailVerified flag without
 * requiring a full sign-out / sign-in cycle.
 */
async function checkEmailVerified() {
    const checkBtn = document.getElementById('checkVerifiedBtn');
    const status   = document.getElementById('verificationStatus');
    const user     = _pendingVerificationUser || firebase.auth().currentUser;
    if (!user) return;

    if (checkBtn) checkBtn.disabled = true;
    if (status) { status.textContent = 'Checking…'; status.className = 'verification-status loading'; }

    try {
        await user.reload();
        const refreshed = firebase.auth().currentUser;

        if (refreshed?.emailVerified) {
            if (status) { status.textContent = 'Verified! Loading your workspace…'; status.className = 'verification-status success'; }
            _pendingVerificationUser = null;
            _hideVerificationModal();

            // Manually complete the sign-in flow that was paused.
            const wasGuest = !_currentUser;
            _currentUser = refreshed;
            await onboardUser(refreshed);
            if (wasGuest && window.app) {
                window.app.guestMode = false;
                await window.app.exitGuestMode();
            }
            updateAuthButtons();
        } else {
            if (status) { status.textContent = 'Not verified yet — click the link in your email first.'; status.className = 'verification-status error'; }
            if (checkBtn) checkBtn.disabled = false;
        }
    } catch (e) {
        console.error('[AUTH] checkEmailVerified reload failed:', e);
        if (status) { status.textContent = `Error: ${e.message}`; status.className = 'verification-status error'; }
        if (checkBtn) checkBtn.disabled = false;
    }
}

// ── Playground helpers ─────────────────────────────────────────────────────

/** Show the sandbox reset confirmation toast. */
function showSandboxConfirmToast() {
    const toast = document.getElementById('sandboxToast');
    if (!toast) return;
    toast.setAttribute('aria-hidden', 'false');
    toast.classList.add('visible');
    if (typeof lucide !== 'undefined') lucide.createIcons();
}

/** Hide the sandbox reset confirmation toast. */
function hideSandboxConfirmToast() {
    const toast = document.getElementById('sandboxToast');
    if (!toast) return;
    toast.classList.remove('visible');
    toast.setAttribute('aria-hidden', 'true');
}

/**
 * Show a brief auto-dismissing status toast.
 * @param {'success'|'error'} type
 * @param {string} message
 */
function showSandboxStatusToast(type, message) {
    // Reuse an existing element or create one on the fly.
    let el = document.getElementById('sandboxStatusToast');
    if (!el) {
        el = document.createElement('div');
        el.id = 'sandboxStatusToast';
        el.className = 'sandbox-status-toast';
        document.body.appendChild(el);
    }
    el.textContent = message;
    el.className   = `sandbox-status-toast ${type}`;
    // Force reflow so the CSS transition fires even on back-to-back calls.
    el.offsetHeight; // eslint-disable-line no-unused-expressions
    el.classList.add('visible');
    setTimeout(() => el.classList.remove('visible'), 3000);
}

/** Show the playground guest-quota modal and lock the chat input. */
function showQuotaModal() {
    const modal = document.getElementById('quotaModal');
    if (!modal) return;
    modal.classList.add('active');
    if (typeof lucide !== 'undefined') lucide.createIcons();

    // Hard-block the chat input — only restored after a real sign-in.
    if (window.app) {
        window.app.promptInput.disabled = true;
        window.app.promptInput.placeholder = 'Sign in to continue…';
        window.app.sendBtn.disabled = true;
    }
}

/** Hide the playground guest-quota modal. */
function hideQuotaModal() {
    document.getElementById('quotaModal')?.classList.remove('active');
}

/**
 * "Return to Home" action from the quota modal.
 * Exits playground mode visually, then signs out the anonymous Firebase session
 * so the app returns to proper guest/sign-in state with the input locked.
 */
async function exitPlaygroundFromQuota() {
    hideQuotaModal();

    // Exit playground mode UI (deactivate toggle button, remove theme, restore dropdowns).
    const queryChinookBtn = document.getElementById('queryChinookBtn');
    if (queryChinookBtn) queryChinookBtn.classList.remove('active');
    document.body.classList.remove('active-playground');
    window.app?.setPlaygroundMode?.(false);

    // Sign out the anonymous Firebase session so onAuthStateChanged fires with null,
    // returning the app to an unauthenticated guest state (input stays locked).
    if (_currentUser?.isAnonymous) {
        try { await firebase.auth().signOut(); } catch (e) {
            console.warn('[Quota] anonymous sign-out error:', e);
        }
        // Explicitly keep input locked in case onAuthStateChanged fires slowly.
        if (window.app) {
            window.app.promptInput.disabled = true;
            window.app.promptInput.placeholder = 'Sign in to start chatting…';
            window.app.sendBtn.disabled = true;
        }
    }
}

/**
 * Fetch the caller's playground_count from Cosmos via GET /api/profile.
 * Returns 0 when the profile cannot be read (e.g. anonymous user not yet created).
 */
async function checkPlaygroundQuota() {
    try {
        const res = await apiFetch('/api/profile');
        if (!res.ok) return 0;
        const data = await res.json();
        return typeof data.playground_count === 'number' ? data.playground_count : 0;
    } catch (e) {
        if (!e.guestMode) console.warn('[Playground] checkPlaygroundQuota error:', e);
        return 0;
    }
}

/** Confirm handler — call POST /api/playground/reset with progress modal feedback. */
async function executePlaygroundReset() {
    const confirmBtn = document.getElementById('sandboxConfirmBtn');
    if (confirmBtn) confirmBtn.disabled = true;
    hideSandboxConfirmToast();

    // ── Open the progress modal as a visual loading indicator ────────────
    const app = window.app;
    app?._showIndexingModal?.('playground');
    app?._updateIndexingProgress?.(0, 'Copying chinook_master.db → playground.db…', null);

    // Animate bar to ~45 % while the network request is in flight.
    let fakePct = 0;
    const fakeTimer = setInterval(() => {
        fakePct = Math.min(fakePct + 9, 45);
        app?._updateIndexingProgress?.(fakePct, 'Restoring database file…', null);
    }, 120);

    try {
        const res = await apiFetch('/api/playground/reset', { method: 'POST' });
        clearInterval(fakeTimer);

        if (res.ok) {
            // Sweep to 100 %, brief pause, then close + toast.
            app?._updateIndexingProgress?.(100, '✔ Sandbox restored from master', 0);
            setTimeout(() => {
                app?._hideIndexingModal?.();
                showSandboxStatusToast('success', '✔ Sandbox reset — playground.db restored from master.');
            }, 1200);
            console.log('[Playground] reset successful');
        } else {
            const err = await res.text().catch(() => String(res.status));
            app?._hideIndexingModal?.();
            showSandboxStatusToast('error', `Reset failed: ${err}`);
            console.error('[Playground] reset failed:', err);
        }
    } catch (e) {
        clearInterval(fakeTimer);
        app?._hideIndexingModal?.();
        showSandboxStatusToast('error', `Reset error: ${e.message}`);
        console.error('[Playground] reset error:', e);
    } finally {
        if (confirmBtn) {
            confirmBtn.disabled = false;
            confirmBtn.innerHTML = '<i data-lucide="rotate-ccw"></i> Yes, Reset';
            if (typeof lucide !== 'undefined') lucide.createIcons();
        }
    }
}

/** Cancel button — sign out fully and return to guest/sign-in state. */
async function cancelVerification() {
    _pendingVerificationUser = null;
    _hideVerificationModal();
    await firebase.auth().signOut();   // clears the unverified session
}

/** Returns a fresh Firebase ID token for the signed-in user. */
async function getApiToken() {
    if (!_currentUser) {
        throw Object.assign(new Error('Not signed in'), { guestMode: true });
    }
    return _currentUser.getIdToken();
}

// ── Shared helpers ─────────────────────────────────────────────────────────

function showAuthGate() { /* no-op */ }
function hideAuthGate()  { /* no-op */ }

function updateAuthButtons() {
    const hasAccount = isAuthenticated();
    const el         = (id) => document.getElementById(id);

    // Avatar: Lucide icon (guest) vs green-gradient initials (authenticated)
    if (el('avatarIcon'))     el('avatarIcon').style.display     = hasAccount ? 'none' : '';
    if (el('avatarInitials')) el('avatarInitials').style.display = hasAccount ? '' : 'none';

    // Display name next to avatar: only when signed in
    if (el('userDisplayName')) el('userDisplayName').style.display = hasAccount ? '' : 'none';

    // Dropdown: guest section vs auth section
    if (el('profileGuestItems'))    el('profileGuestItems').style.display    = hasAccount ? 'none' : '';
    if (el('profileAuthItems'))     el('profileAuthItems').style.display     = hasAccount ? '' : 'none';
    if (el('dropdownSignOut'))      el('dropdownSignOut').style.display      = hasAccount ? '' : 'none';
    if (el('profileDropdownHeader'))el('profileDropdownHeader').style.display= hasAccount ? '' : 'none';
    if (el('profileHeaderDivider')) el('profileHeaderDivider').style.display = hasAccount ? '' : 'none';

    if (hasAccount && _currentUser) {
        const email       = _currentUser.email || '';
        const displayName = _currentUser.displayName || email.split('@')[0] || 'User';

        const parts    = displayName.trim().split(/\s+/);
        const initials = parts.length >= 2
            ? (parts[0][0] + parts[parts.length - 1][0]).toUpperCase()
            : displayName.slice(0, 2).toUpperCase();

        if (el('userDisplayName')) el('userDisplayName').textContent = displayName;
        if (el('avatarInitials'))  el('avatarInitials').textContent  = initials;
        if (el('avatarLg'))        el('avatarLg').textContent        = initials;
        if (el('profileName'))     el('profileName').textContent     = displayName;
        if (el('profileEmail'))    el('profileEmail').textContent    = email;
    }
}

/**
 * Fetch wrapper that attaches a Firebase ID token as a Bearer header.
 * Unauthenticated callers receive a typed guestMode error; upstream
 * code (loadSettings, loadConversations) already catches and skips silently.
 */
async function apiFetch(url, options = {}) {
    const opts = { ...options };
    opts.headers = opts.headers || {};
    try {
        const token = await getApiToken();
        opts.headers['Authorization'] = 'Bearer ' + token;
    } catch (e) {
        if (e.guestMode) throw e; // let caller decide
        throw e;
    }
    const res = await fetch(url, opts);
    if (res.status === 401) {
        throw Object.assign(
            new Error('Session expired. Please sign in again.'),
            { sessionExpired: true }
        );
    }
    return res;
}

/**
 * After a successful sign-in, POST the Firebase ID token to the backend so it
 * can upsert the user record in Cosmos DB (the Data Plane onboarding step).
 */
async function onboardUser(user) {
    try {
        const idToken = await user.getIdToken();
        const res = await fetch('/api/auth/onboard', {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${idToken}`,
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                uid:          user.uid,
                username:     user.email,
                display_name: user.displayName || '',
            }),
        });
        if (res.ok) {
            const data = await res.json();
            console.log('[AUTH] Onboarding complete:', data);
        } else {
            console.warn('[AUTH] Onboarding non-OK:', res.status);
        }
    } catch (e) {
        console.error('[AUTH] Onboarding error:', e);
    }
}

// ── Profile management ─────────────────────────────────────────────────────

let _recaptchaVerifier       = null;   // firebase.auth.RecaptchaVerifier
let _phoneConfirmationResult = null;   // ConfirmationResult from verifyPhoneNumber
let _pendingVerificationId   = null;   // verificationId for PhoneAuthProvider.credential

/** Show/hide the profile status banner inside the profile modal. */
function _pfStatus(msg, type = 'loading') {
    const el = document.getElementById('profileStatus');
    if (!el) return;
    el.textContent = msg;
    el.className   = `pf-status pf-status--${type}`;
    el.style.display = msg ? '' : 'none';
}

/** Open the Edit Profile modal and pre-populate fields from the current user. */
function openProfileModal() {
    if (!_currentUser) return;
    const modal      = document.getElementById('profileModal');
    const nameInput  = document.getElementById('profileNameInput');
    const phoneInput = document.getElementById('profilePhoneInput');

    if (nameInput)  nameInput.value  = _currentUser.displayName || '';
    if (phoneInput) phoneInput.value = _currentUser.phoneNumber  || '';

    // Reset SMS step
    const smsGroup = document.getElementById('smsCodeGroup');
    if (smsGroup) smsGroup.style.display = 'none';
    _pfStatus('', '');

    modal?.classList.add('active');

    // Initialise reCAPTCHA here (inside the modal context) so its hidden
    // iframe does not sit over the main page and intercept click events.
    // Only creates a new instance if the previous one was cleared (e.g. expired).
    if (!_recaptchaVerifier) {
        try {
            _recaptchaVerifier = new firebase.auth.RecaptchaVerifier(
                'recaptcha-container',
                {
                    size: 'invisible',
                    callback: () => console.log('[reCAPTCHA] Solved and ready'),
                    'expired-callback': () => {
                        console.warn('[reCAPTCHA] Token expired');
                        _recaptchaVerifier = null;
                    },
                },
            );
            _recaptchaVerifier.render()
                .then((id) => console.log('[reCAPTCHA] Widget ready, id:', id))
                .catch((e) => {
                    console.warn('[reCAPTCHA] render() failed:', e.message);
                    _recaptchaVerifier = null;
                });
        } catch (e) {
            console.warn('[reCAPTCHA] Init failed:', e.message);
            _recaptchaVerifier = null;
        }
    }
}

/**
 * Update the Firebase Auth display name and sync to Cosmos DB.
 * Called when the user clicks "Save Name".
 */
async function updateDisplayName() {
    if (!_currentUser) return;
    const nameInput = document.getElementById('profileNameInput');
    const newName   = (nameInput?.value || '').trim();
    if (!newName) { _pfStatus('Display name cannot be empty.', 'error'); return; }

    _pfStatus('Saving name…');
    try {
        // 1. Update Firebase Auth profile
        await _currentUser.updateProfile({ displayName: newName });

        // 2. Sync to Cosmos DB via backend
        await _syncProfileToBackend({ display_name: newName });

        // 3. Refresh the nav strip
        updateAuthButtons();
        _pfStatus('Display name updated.', 'success');
    } catch (err) {
        console.error('[PROFILE] updateDisplayName error:', err);
        _pfStatus(`Error: ${err.message}`, 'error');
    }
}

/**
 * Start the phone-number verification flow.
 * Creates an invisible reCAPTCHA, sends an SMS, and stores the verificationId.
 * Called when the user clicks "Send Code".
 *
 * NOTE: Requires Firebase Phone Auth enabled in the Firebase Console *and* a
 * billing plan (Blaze) because SMS is a paid service.
 */
async function initPhoneVerification() {
    if (!_currentUser) return;
    const phoneInput = document.getElementById('profilePhoneInput');
    const phone      = (phoneInput?.value || '').trim();
    if (!phone) { _pfStatus('Enter a phone number first.', 'error'); return; }

    _pfStatus('Sending verification code…');
    try {
        // Reuse the verifier initialised when the profile modal opened.
        // If it was cleared (token expired or previous error), recreate it now.
        if (!_recaptchaVerifier) {
            _recaptchaVerifier = new firebase.auth.RecaptchaVerifier(
                'recaptcha-container',
                { size: 'invisible', callback: () => {} },
            );
            await _recaptchaVerifier.render();
        }

        // PhoneAuthProvider.verifyPhoneNumber returns a verificationId.
        const provider         = new firebase.auth.PhoneAuthProvider();
        _pendingVerificationId = await provider.verifyPhoneNumber(phone, _recaptchaVerifier);

        // Show the SMS code input row.
        const smsGroup = document.getElementById('smsCodeGroup');
        if (smsGroup) smsGroup.style.display = '';
        document.getElementById('smsCodeInput')?.focus();

        _pfStatus('Code sent! Enter it below.', 'success');
    } catch (err) {
        console.error('[PROFILE] initPhoneVerification error:', err);
        _pfStatus(`Error: ${err.message}`, 'error');
        // On any error, clear the verifier so it's recreated fresh on the next attempt.
        if (_recaptchaVerifier) { _recaptchaVerifier.clear(); _recaptchaVerifier = null; }
    }
}

/**
 * Confirm the SMS code and link the phone number to the Firebase account.
 * Called when the user clicks "Verify".
 */
async function verifyPhoneCode() {
    if (!_currentUser || !_pendingVerificationId) return;
    const codeInput = document.getElementById('smsCodeInput');
    const code      = (codeInput?.value || '').trim();
    if (code.length !== 6) { _pfStatus('Enter the 6-digit code from the SMS.', 'error'); return; }

    _pfStatus('Verifying code…');
    try {
        // Build a PhoneAuthCredential from the verificationId + user code.
        const credential = firebase.auth.PhoneAuthProvider.credential(
            _pendingVerificationId,
            code,
        );

        // updatePhoneNumber links the credential to the signed-in account.
        await _currentUser.updatePhoneNumber(credential);

        // Sync the verified number to Cosmos DB.
        await _syncProfileToBackend({ phone_number: _currentUser.phoneNumber });

        // Hide the SMS row and reset state.
        document.getElementById('smsCodeGroup').style.display = 'none';
        if (codeInput) codeInput.value = '';
        _pendingVerificationId = null;

        _pfStatus('Phone number verified and saved.', 'success');
    } catch (err) {
        console.error('[PROFILE] verifyPhoneCode error:', err);
        // err.code === 'auth/invalid-verification-code' → wrong code
        // err.code === 'auth/requires-recent-login'     → user must re-authenticate
        const msg = err.code === 'auth/requires-recent-login'
            ? 'Re-authentication required. Please sign out and sign back in, then try again.'
            : `Error: ${err.message}`;
        _pfStatus(msg, 'error');
    }
}

/**
 * PATCH /api/profile — push profile field changes to Cosmos DB.
 * Fields: { display_name?, phone_number? }
 */
async function _syncProfileToBackend(fields) {
    try {
        const res = await apiFetch('/api/profile', {
            method:  'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify(fields),
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            console.warn('[PROFILE] Backend sync failed:', err.detail || res.status);
        }
    } catch (e) {
        if (!e.guestMode) console.error('[PROFILE] _syncProfileToBackend error:', e);
    }
}

// Supported LLM providers (order for display)
const SUPPORTED_LLM_PROVIDERS = [
    'ollama', 'openai', 'anthropic', 'gemini', 'azure',
    'groq', 'deepseek', 'mistral', 'nvidia', 'alibaba', 'meta'
];

// UI Templates for settings modal (switch/case logic)
const LLM_TEMPLATES = {
    ollama: { label: 'Ollama', fields: ['endpoint', 'model'], endpointDefault: 'http://localhost:11434', needsApiKey: false },
    openai: { label: 'OpenAI', fields: ['api_key', 'model'], needsApiKey: true },
    anthropic: { label: 'Anthropic', fields: ['api_key', 'model'], needsApiKey: true },
    gemini: { label: 'Google Gemini', fields: ['api_key', 'model'], needsApiKey: true },
    azure: { label: 'Azure OpenAI', fields: ['api_key', 'endpoint', 'deployment'], needsApiKey: true },
    groq: { label: 'Groq', fields: ['api_key', 'model'], needsApiKey: true },
    deepseek: { label: 'DeepSeek', fields: ['api_key', 'model'], needsApiKey: true },
    mistral: { label: 'Mistral AI', fields: ['api_key', 'model'], needsApiKey: true },
    nvidia: { label: 'Nvidia NIM', fields: ['api_key', 'model'], needsApiKey: true },
    alibaba: { label: 'Alibaba Cloud', fields: ['api_key', 'model', 'endpoint'], needsApiKey: true, endpointDefault: 'https://dashscope-intl.aliyuncs.com/compatible-mode/v1' },
    meta: { label: 'Meta (Llama)', fields: ['api_key', 'model', 'endpoint'], needsApiKey: true }
};

const DB_TEMPLATES = {
    mysql: { fields: ['host', 'port', 'user', 'password', 'database'], defaultPort: 3306 },
    postgres: { fields: ['host', 'port', 'user', 'password', 'database'], defaultPort: 5432 },
    oracle: { fields: ['host', 'port', 'service_name', 'user', 'password'], defaultPort: 1521 },
    sqlserver: { fields: ['host', 'port', 'user', 'password', 'database'], defaultPort: 1433 }
};

const CLOUD_PROVIDERS = {
    aws: { fields: ['region', 'secret_arn'] },
    azure: { fields: ['region', 'instance'] }
};

class DaiBaiApp {
    constructor() {
        this.conversationId = null;
        this.ws = null;
        this.isLoading = false;
        this.lastGeneratedSql = null;
        this.resultsCache = {};  // resultsId -> results for Export CSV
        this.sessionMessages = [];  // messages in current conversation for prompts list
        this.attachedFiles = [];   // [{ id, name, size }] for file upload
        this.guestMode = !isAuthenticated();

        this.init();
    }
    
    async init() {
        this.bindElements();
        this.loadPreferences();
        this.bindEvents();
        updateAuthButtons();

        if (this.guestMode) {
            // Restricted mode: show the UI immediately, defer all backend calls.
            this.enterGuestMode();
            return;
        }

        // Authenticated: load full feature set.
        await this.loadSettings();
        await this.loadConversations();
        this.connectWebSocket();
    }
    
    enterGuestMode() {
        // Disable the chat input and send button until the user authenticates.
        this.promptInput.disabled = true;
        this.promptInput.placeholder = 'Sign in to start chatting…';
        this.sendBtn.disabled = true;

        // Show an empty but clearly labelled sidebar state.
        this.databaseSelect.innerHTML = '<option value="">— sign in to connect —</option>';
        this.llmSelect.innerHTML = '<option value="">— sign in to connect —</option>';

        // Inject a guest banner above the conversation list.
        const banner = document.createElement('div');
        banner.id = 'guestBanner';
        banner.className = 'guest-banner';
        banner.innerHTML = `
            <p><strong>Guest Mode</strong></p>
            <p>Sign in to save conversations, access history, and connect your databases.</p>
            <button class="btn-primary guest-signin-btn">Sign In</button>
        `;
        banner.querySelector('.guest-signin-btn').addEventListener('click', () => signIn());
        this.conversationList.before(banner);
    }

    /**
     * Called by onAuthStateChanged when a user signs in while the app is
     * already running in guest mode. Reverses enterGuestMode() and loads
     * the full authenticated feature set.
     */
    async exitGuestMode() {
        // Remove the guest banner.
        document.getElementById('guestBanner')?.remove();

        // Re-enable the chat input.
        this.promptInput.disabled = false;
        this.promptInput.placeholder = 'Ask me about your database…';

        // Load the full feature set.
        await this.loadSettings();
        await this.loadConversations();
        this.connectWebSocket();

        console.log('[AUTH] Guest mode exited — full feature set loaded.');
    }

    /**
     * Called by the "Query Chinook DB" sidebar button.
     * Updates the module-level flag and re-enables/disables the chat input for
     * anonymous users who enter playground mode via guest access.
     */
    setPlaygroundMode(active) {
        _isPlaygroundActive = active;

        // ── Body class for global CSS overrides ──────────────────────────
        document.body.classList.toggle('playground-active', active);

        if (active) {
            // ── Lock the nav dropdowns to Chinook / Sandbox LLM ──────────
            // Save current HTML so we can restore exactly on exit.
            this._savedDbHtml  = this.databaseSelect.outerHTML.includes('chinook_playground')
                ? this._savedDbHtml  // already saved from a previous enter
                : this.databaseSelect.innerHTML;
            this._savedLlmHtml = this.llmSelect.outerHTML.includes('_sandbox')
                ? this._savedLlmHtml
                : this.llmSelect.innerHTML;
            this._savedDbValue  = this.databaseSelect.value;
            this._savedLlmValue = this.llmSelect.value;

            this.databaseSelect.innerHTML =
                '<option value="chinook_playground">Chinook (SQLite)</option>';
            this.databaseSelect.value    = 'chinook_playground';
            this.databaseSelect.disabled = true;

            this.llmSelect.innerHTML =
                '<option value="_sandbox">GPT-4o-Mini (Sandbox)</option>';
            this.llmSelect.value    = '_sandbox';
            this.llmSelect.disabled = true;

            // ── Anonymous-guest tooltip ───────────────────────────────────
            const anonMsg = _currentUser?.isAnonymous
                ? 'Sign in to connect your own data.'
                : 'Locked while Playground mode is active.';
            this.databaseSelect.title = anonMsg;
            this.llmSelect.title      = anonMsg;

            // ── Re-enable the chat input for anonymous/guest users ────────
            if (_currentUser?.isAnonymous) {
                this.promptInput.disabled = false;
                this.promptInput.placeholder = 'Ask me about the Chinook database…';
                this.sendBtn.disabled = false;
            }

        } else {
            // ── Unlock and restore the nav dropdowns ─────────────────────
            this.databaseSelect.disabled = false;
            this.llmSelect.disabled      = false;
            this.databaseSelect.title    = '';
            this.llmSelect.title         = '';

            // Reload from server to restore real options & persisted selection.
            this.loadSettings().catch(() => {
                // Fallback: restore saved HTML directly if API is unreachable.
                if (this._savedDbHtml)  this.databaseSelect.innerHTML = this._savedDbHtml;
                if (this._savedLlmHtml) this.llmSelect.innerHTML      = this._savedLlmHtml;
                if (this._savedDbValue)  this.databaseSelect.value    = this._savedDbValue;
                if (this._savedLlmValue) this.llmSelect.value         = this._savedLlmValue;
            });

            // Hide the index nudge (the restored DB may already be indexed).
            this._hideIndexNudge();
        }

        console.log('[Playground] _isPlaygroundActive =', active);
    }

    // ── Schema Index Status ────────────────────────────────────────────────

    /** Update the status dot next to the Database label.
     * @param {'unknown'|'indexed'|'not-indexed'|'indexing'} state */
    _updateDbStatusDot(state) {
        const dot = document.getElementById('dbStatusDot');
        if (!dot) return;
        dot.classList.remove('status-green', 'status-red', 'status-pulse');
        switch (state) {
            case 'indexed':
                dot.classList.add('status-green');
                dot.title = 'Indexed for AI search';
                break;
            case 'not-indexed':
                dot.classList.add('status-red');
                dot.title = 'Not indexed — click "Index now" to enable AI search';
                break;
            case 'indexing':
                dot.classList.add('status-pulse');
                dot.title = 'Indexing in progress…';
                break;
            default:
                dot.title = '';
        }
    }

    /** Show the "Not indexed" nudge bar below the nav dropdowns. */
    _showIndexNudge() {
        const el = document.getElementById('dbIndexNudge');
        if (el) el.style.display = 'flex';
        if (typeof lucide !== 'undefined') lucide.createIcons();
    }

    /** Hide the "Not indexed" nudge bar. */
    _hideIndexNudge() {
        const el = document.getElementById('dbIndexNudge');
        if (el) el.style.display = 'none';
    }

    /** Open the indexing progress modal for the given database. */
    _showIndexingModal(dbId) {
        const modal = document.getElementById('indexingModal');
        if (!modal) return;
        // Reset to initial state.
        this._updateIndexingProgress(0, `Vectorizing "${dbId}"…`, null);
        document.getElementById('indexingStatusLine').textContent = 'Starting…';
        modal.classList.add('active');
        if (typeof lucide !== 'undefined') lucide.createIcons();
    }

    /** Close the indexing progress modal. */
    _hideIndexingModal() {
        document.getElementById('indexingModal')?.classList.remove('active');
    }

    /**
     * Update the progress bar, percentage, status line, and ETA.
     * @param {number} pct      0–100
     * @param {string} status   Status message from the server
     * @param {number|null} eta Seconds remaining (null = not yet known)
     */
    _updateIndexingProgress(pct, status, eta) {
        const bar    = document.getElementById('indexingBar');
        const pctEl  = document.getElementById('indexingPct');
        const etaEl  = document.getElementById('indexingEta');
        const statEl = document.getElementById('indexingStatusLine');
        const track  = document.getElementById('indexingTrack');

        if (bar)    bar.style.width    = `${Math.min(100, pct)}%`;
        if (pctEl)  pctEl.textContent  = `${Math.round(pct)}%`;
        if (statEl) statEl.textContent = status || '';
        if (track)  track.setAttribute('aria-valuenow', Math.round(pct));

        if (etaEl) {
            if (eta === null || eta === undefined) {
                etaEl.textContent = 'Calculating…';
            } else if (eta <= 0) {
                etaEl.textContent = 'Almost done';
            } else if (eta < 60) {
                etaEl.textContent = `~${Math.ceil(eta)}s remaining`;
            } else {
                const m = Math.floor(eta / 60);
                const s = Math.ceil(eta % 60);
                etaEl.textContent = `~${m}m ${s}s remaining`;
            }
        }
    }

    /**
     * Call GET /api/schema/status/{dbId}.  Updates the dot and shows/hides the nudge.
     * Safe to call in guest mode — silently no-ops.
     */
    async checkDbIndexStatus(dbId) {
        if (!dbId || !isAuthenticated()) {
            this._updateDbStatusDot('unknown');
            this._hideIndexNudge();
            return;
        }
        this._updateDbStatusDot('unknown');
        try {
            const res  = await apiFetch(`/api/schema/status/${encodeURIComponent(dbId)}`);
            const data = await res.json();
            if (data.is_indexed) {
                this._updateDbStatusDot('indexed');
                this._hideIndexNudge();
                console.log(`[Schema] "${dbId}" indexed at ${data.last_indexed_at}`);
            } else {
                this._updateDbStatusDot('not-indexed');
                this._showIndexNudge();
                console.log(`[Schema] "${dbId}" not yet indexed`);
            }
        } catch (e) {
            if (!e.guestMode) console.warn('[Schema] Status check failed:', e);
        }
    }

    /**
     * Open /ws/schema-progress and stream indexing progress into the modal.
     * Automatically closes the modal and updates the status dot on completion.
     */
    async startSchemaIndexing(dbId) {
        if (!dbId) return;
        let token;
        try { token = await getApiToken(); }
        catch (e) { console.error('[Schema] Cannot start indexing — not authenticated'); return; }

        const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${proto}//${location.host}/ws/schema-progress`
                    + `?token=${encodeURIComponent(token)}&db=${encodeURIComponent(dbId)}`;

        this._showIndexingModal(dbId);
        this._updateDbStatusDot('indexing');
        this._hideIndexNudge();

        const ws = new WebSocket(wsUrl);

        ws.onopen = () => console.log(`[Schema] WS open for "${dbId}"`);

        ws.onmessage = ({ data }) => {
            let msg;
            try { msg = JSON.parse(data); } catch { return; }

            if (msg.type === 'progress') {
                this._updateIndexingProgress(msg.pct, msg.status, msg.eta);

            } else if (msg.type === 'done') {
                this._updateIndexingProgress(100, msg.status, 0);
                // Show completed state briefly, then close modal.
                setTimeout(() => {
                    this._hideIndexingModal();
                    this._updateDbStatusDot('indexed');
                }, 1400);

            } else if (msg.type === 'error') {
                console.error('[Schema] Indexing error:', msg.message);
                this._hideIndexingModal();
                this._updateDbStatusDot('not-indexed');
                this._showIndexNudge();
                showSandboxStatusToast('error', `Indexing failed: ${msg.message}`);
            }
        };

        ws.onerror = (e) => {
            console.error('[Schema] WS error', e);
            this._hideIndexingModal();
            this._updateDbStatusDot('not-indexed');
            this._showIndexNudge();
        };

        ws.onclose = () => console.log(`[Schema] WS closed for "${dbId}"`);
    }

    loadPreferences() {
        const prefs = JSON.parse(localStorage.getItem('daibai_preferences') || '{}');
        
        // Auto-copy defaults to true
        this.autoCopyCheckbox.checked = prefs.autoCopy !== false;
        
        // Auto-CSV defaults to false
        this.autoCsvCheckbox.checked = prefs.autoCsv === true;
        
        // Execute checkbox
        this.executeCheckbox.checked = prefs.autoExecute === true;
        
        // Sidebar state
        if (prefs.sidebarCollapsed) {
            this.sidebar.classList.add('collapsed');
        }
    }
    
    savePreferences() {
        const prefs = {
            autoCopy: this.autoCopyCheckbox.checked,
            autoCsv: this.autoCsvCheckbox.checked,
            autoExecute: this.executeCheckbox.checked,
            sidebarCollapsed: this.sidebar.classList.contains('collapsed'),
            database: this.databaseSelect.value,
            llm: this.llmSelect.value,
            mode: this.modeSelect.value
        };
        localStorage.setItem('daibai_preferences', JSON.stringify(prefs));
    }
    
    async handleFileSelect(e) {
        const files = Array.from(e.target.files || []);
        e.target.value = '';
        for (const file of files) {
            try {
                const formData = new FormData();
                formData.append('file', file);
                const res = await apiFetch('/api/upload', { method: 'POST', body: formData });
                if (res.ok) {
                    const data = await res.json();
                    this.attachedFiles.push({ id: data.id, name: data.name, size: data.size });
                    this.renderAttachedFiles();
                } else {
                    const err = await res.json();
                    alert('Upload failed: ' + (err.detail || 'Unknown error'));
                }
            } catch (err) {
                alert('Upload failed: ' + (err.message || 'Network error'));
            }
        }
    }
    
    removeAttachedFile(id) {
        this.attachedFiles = this.attachedFiles.filter(f => f.id !== id);
        this.renderAttachedFiles();
    }
    
    renderAttachedFiles() {
        if (!this.attachedFilesEl) return;
        if (this.attachedFiles.length === 0) {
            this.attachedFilesEl.innerHTML = '';
            this.attachedFilesEl.style.display = 'none';
            return;
        }
        this.attachedFilesEl.style.display = 'flex';
        this.attachedFilesEl.innerHTML = this.attachedFiles.map(f => `
            <span class="file-chip" data-id="${f.id}">
                <span class="file-chip-name">${this.escapeHtml(f.name)}</span>
                <button class="file-chip-remove" data-id="${f.id}" title="Remove">×</button>
            </span>
        `).join('');
        this.attachedFilesEl.querySelectorAll('.file-chip-remove').forEach(btn => {
            btn.addEventListener('click', () => this.removeAttachedFile(btn.dataset.id));
        });
    }
    
    copyToClipboard(text) {
        if (text && this.autoCopyCheckbox.checked) {
            navigator.clipboard.writeText(text).catch(err => {
                console.error('Failed to copy:', err);
            });
        }
    }
    
    saveToCsv(results) {
        if (!results || results.length === 0 || !this.autoCsvCheckbox.checked) {
            return;
        }
        
        // Generate CSV content
        const columns = Object.keys(results[0]);
        const csvRows = [];
        
        // Header row
        csvRows.push(columns.map(col => `"${col}"`).join(','));
        
        // Data rows
        for (const row of results) {
            const values = columns.map(col => {
                const val = row[col];
                if (val === null || val === undefined) return '';
                const str = String(val).replace(/"/g, '""');
                return `"${str}"`;
            });
            csvRows.push(values.join(','));
        }
        
        const csvContent = csvRows.join('\n');
        
        // Generate filename with timestamp
        const timestamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
        const filename = `daibai_results_${timestamp}.csv`;
        
        // Download file
        const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
        const link = document.createElement('a');
        link.href = URL.createObjectURL(blob);
        link.download = filename;
        link.click();
        URL.revokeObjectURL(link.href);
    }
    
    bindElements() {
        // Navigation
        this.sidebarToggle = document.getElementById('sidebarToggle');
        this.sidebar = document.getElementById('sidebar');
        this.databaseSelect = document.getElementById('databaseSelect');
        this.llmSelect = document.getElementById('llmSelect');
        this.modeSelect = document.getElementById('modeSelect');
        this.autoCopyCheckbox = document.getElementById('autoCopyCheckbox');
        this.autoCsvCheckbox = document.getElementById('autoCsvCheckbox');
        this.schemaBtn = document.getElementById('schemaBtn');
        this.schemaModal = document.getElementById('schemaModal');
        this.schemaModalClose = document.getElementById('schemaModalClose');
        this.schemaContent = document.getElementById('schemaContent');
        this.settingsBtn = document.getElementById('settingsBtn');
        this.settingsModal = document.getElementById('settingsModal');
        this.settingsModalClose = document.getElementById('settingsModalClose');
        this.settingsContent = document.getElementById('settingsContent');
        this.settingsSave = document.getElementById('settingsSave');
        this.settingsCancel = document.getElementById('settingsCancel');
        
        // Sidebar
        this.newChatBtn = document.getElementById('newChatBtn');
        this.conversationList = document.getElementById('conversationList');
        
        // Chat
        this.messagesContainer = document.getElementById('messagesContainer');
        this.welcomeMessage = document.getElementById('welcomeMessage');
        this.promptInput = document.getElementById('promptInput');
        this.sendBtn = document.getElementById('sendBtn');
        this.executeCheckbox = document.getElementById('executeCheckbox');
        this.attachBtn = document.getElementById('attachBtn');
        this.fileInput = document.getElementById('fileInput');
        this.attachedFilesEl = document.getElementById('attachedFiles');
    }
    
    bindEvents() {
        // Sidebar toggle
        this.sidebarToggle.addEventListener('click', () => {
            this.sidebar.classList.toggle('collapsed');
            this.savePreferences();
        });
        
        // Settings changes
        this.databaseSelect.addEventListener('change', () => {
            this.updateSettings();
            this.savePreferences();
            this.checkDbIndexStatus(this.databaseSelect.value);
        });

        // Schema index nudge — "Index now" button
        document.getElementById('dbIndexBtn')?.addEventListener('click', () => {
            this.startSchemaIndexing(this.databaseSelect.value);
        });
        this.llmSelect.addEventListener('change', () => {
            this.updateSettings();
            this.savePreferences();
        });
        this.modeSelect.addEventListener('change', () => this.savePreferences());
        this.autoCopyCheckbox.addEventListener('change', () => this.savePreferences());
        this.autoCsvCheckbox.addEventListener('change', () => this.savePreferences());
        this.executeCheckbox.addEventListener('change', () => this.savePreferences());
        

        // Profile avatar dropdown
        const profileAvatarBtn = document.getElementById('profileAvatarBtn');
        const profileDropdown  = document.getElementById('profileDropdown');
        if (profileAvatarBtn && profileDropdown) {
            profileAvatarBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                const isOpen = profileDropdown.classList.toggle('open');
                profileAvatarBtn.setAttribute('aria-expanded', isOpen);
            });
            // Close when clicking outside
            document.addEventListener('click', () => {
                profileDropdown.classList.remove('open');
                profileAvatarBtn.setAttribute('aria-expanded', 'false');
            });
        }

        // Dropdown: Settings
        document.getElementById('dropdownSettings')?.addEventListener('click', () => {
            profileDropdown?.classList.remove('open');
            profileAvatarBtn?.setAttribute('aria-expanded', 'false');
            this.showSettings();
        });

        // Dropdown: Sign Out
        document.getElementById('dropdownSignOut')?.addEventListener('click', () => {
            profileDropdown?.classList.remove('open');
            profileAvatarBtn?.setAttribute('aria-expanded', 'false');
            signOut();
        });

        // Schema modal
        this.schemaBtn.addEventListener('click', () => this.showSchema());
        this.schemaModalClose.addEventListener('click', () => {
            this.schemaModal.classList.remove('active');
        });
        this.schemaModal.addEventListener('click', (e) => {
            if (e.target === this.schemaModal) {
                this.schemaModal.classList.remove('active');
            }
        });

        // Settings modal
        if (this.settingsBtn) {
            this.settingsBtn.addEventListener('click', (e) => {
                e.preventDefault();
                this.showSettings();
            });
        }
        this.settingsModalClose.addEventListener('click', () => this.closeSettings());
        this.settingsCancel.addEventListener('click', () => this.closeSettings());
        this.settingsSave.addEventListener('click', () => this.saveSettings());
        this.settingsModal.addEventListener('click', (e) => {
            if (e.target === this.settingsModal) this.closeSettings();
            const navItem = e.target.closest('.settings-nav-item');
            if (navItem && this.settingsActiveTab === 'llm_providers') {
                const provider = navItem.dataset.provider;
                if (!provider) return;
                const main = document.getElementById('settingsLLMMain');
                const selected = this.settingsState?.selected_llm_provider;
                if (selected && main) {
                    this.settingsState.llm_providers = this.settingsState.llm_providers || {};
                    this.settingsState.llm_providers[selected] = this.readLLMFormValues();
                }
                this.settingsState.selected_llm_provider = provider;
                this.settingsModal.querySelectorAll('.settings-nav-item').forEach(n => n.classList.toggle('active', n.dataset.provider === provider));
                if (main) main.innerHTML = this.renderLLMProviderForm(provider, this.settingsState?.llm_providers?.[provider] || {});
                this.bindSettingsDynamicHandlers();
            }
        });
        this.settingsModal.querySelectorAll('.settings-tab').forEach(tab => {
            tab.addEventListener('click', () => this.switchSettingsTab(tab.dataset.tab));
        });
        
        // New chat
        this.newChatBtn.addEventListener('click', () => this.startNewChat());
        
        // Input handling
        this.promptInput.addEventListener('input', () => {
            this.autoResizeTextarea();
            this.updateSendButton();
        });
        
        this.promptInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                this.sendMessage();
            }
        });
        
        this.sendBtn.addEventListener('click', () => this.sendMessage());
        
        if (this.attachBtn && this.fileInput) {
            this.attachBtn.addEventListener('click', () => this.fileInput.click());
            this.fileInput.addEventListener('change', (e) => this.handleFileSelect(e));
        }
        
        // Example prompts
        document.querySelectorAll('.example-prompt').forEach(btn => {
            btn.addEventListener('click', () => {
                this.promptInput.value = btn.dataset.prompt;
                this.updateSendButton();
                this.sendMessage();
            });
        });
    }
    
    autoResizeTextarea() {
        const textarea = this.promptInput;
        textarea.style.height = 'auto';
        textarea.style.height = Math.min(textarea.scrollHeight, 200) + 'px';
    }
    
    updateSendButton() {
        this.sendBtn.disabled = !this.promptInput.value.trim() || this.isLoading;
    }
    
    async loadSettings() {
        try {
            const response = await apiFetch('/api/settings');
            const settings = await response.json();
            const prefs = JSON.parse(localStorage.getItem('daibai_preferences') || '{}');
            
            // Populate database dropdown
            const savedDb = prefs.database && settings.databases.includes(prefs.database) 
                ? prefs.database : settings.current_database;
            this.databaseSelect.innerHTML = settings.databases
                .map(db => `<option value="${db}" ${db === savedDb ? 'selected' : ''}>${db}</option>`)
                .join('');
            // Check indexing status for the freshly-selected database.
            this.checkDbIndexStatus(this.databaseSelect.value);

            // Populate LLM dropdown
            const savedLlm = prefs.llm && settings.llm_providers.includes(prefs.llm)
                ? prefs.llm : settings.current_llm;
            this.llmSelect.innerHTML = settings.llm_providers
                .map(llm => `<option value="${llm}" ${llm === savedLlm ? 'selected' : ''}>${llm}</option>`)
                .join('');
            
            // Set mode from preferences or default
            this.modeSelect.value = prefs.mode || settings.current_mode || 'sql';
            
            // Always sync settings to server to ensure database/LLM is correct
            await this.updateSettings();
        } catch (error) {
            console.error('Failed to load settings:', error);
        }
    }
    
    async updateSettings() {
        try {
            await apiFetch('/api/settings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    database: this.databaseSelect.value,
                    llm: this.llmSelect.value,
                    mode: this.modeSelect.value
                })
            });
        } catch (error) {
            console.error('Failed to update settings:', error);
        }
    }
    
    async loadConversations() {
        try {
            const response = await apiFetch('/api/conversations');
            const conversations = await response.json();
            
            this.conversationList.innerHTML = conversations.map(conv => `
                <div class="conversation-item ${conv.id === this.conversationId ? 'active' : ''}" data-id="${conv.id}">
                    <span class="title">${this.escapeHtml(conv.title)}</span>
                    <button class="delete-btn" data-id="${conv.id}">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <path d="M3 6h18M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
                        </svg>
                    </button>
                </div>
            `).join('');
            
            // Bind conversation click handlers
            this.conversationList.querySelectorAll('.conversation-item').forEach(item => {
                item.addEventListener('click', (e) => {
                    if (!e.target.closest('.delete-btn')) {
                        this.loadConversation(item.dataset.id);
                    }
                });
            });
            
            // Bind delete handlers
            this.conversationList.querySelectorAll('.delete-btn').forEach(btn => {
                btn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    this.deleteConversation(btn.dataset.id);
                });
            });
        } catch (error) {
            console.error('Failed to load conversations:', error);
        }
    }
    
    async loadConversation(id) {
        try {
            const response = await apiFetch(`/api/conversations/${id}`);
            const conversation = await response.json();
            
            this.conversationId = id;
            this.sessionMessages = conversation.messages || [];
            this.welcomeMessage.style.display = 'none';
            
            // Clear and render messages
            this.messagesContainer.innerHTML = '';
            this.sessionMessages.forEach((msg, i) => {
                this.renderMessage(msg, i);
            });
            
            // Update prompts list in sidebar
            this.updatePromptsList(conversation.messages);
            
            // Update sidebar
            this.conversationList.querySelectorAll('.conversation-item').forEach(item => {
                item.classList.toggle('active', item.dataset.id === id);
            });
            
            this.scrollToBottom();
        } catch (error) {
            console.error('Failed to load conversation:', error);
        }
    }
    
    async deleteConversation(id) {
        try {
            await apiFetch(`/api/conversations/${id}`, { method: 'DELETE' });
            
            if (id === this.conversationId) {
                this.startNewChat();
            }
            
            await this.loadConversations();
        } catch (error) {
            console.error('Failed to delete conversation:', error);
        }
    }
    
    startNewChat() {
        this.conversationId = null;
        this.sessionMessages = [];
        this.messagesContainer.innerHTML = '';
        this.messagesContainer.appendChild(this.welcomeMessage);
        this.welcomeMessage.style.display = 'flex';
        this.updatePromptsList([]);
        
        this.conversationList.querySelectorAll('.conversation-item').forEach(item => {
            item.classList.remove('active');
        });
    }
    
    async connectWebSocket() {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        let token;
        try {
            token = await getApiToken();
        } catch (e) {
            return;
        }
        const wsUrl = `${protocol}//${window.location.host}/ws/chat?token=${encodeURIComponent(token)}`;
        this.ws = new WebSocket(wsUrl);
        
        this.ws.onopen = () => {
            console.log('WebSocket connected');
        };
        
        this.ws.onmessage = (event) => {
            const data = JSON.parse(event.data);
            this.handleWebSocketMessage(data);
        };
        
        this.ws.onclose = () => {
            console.log('WebSocket disconnected, reconnecting...');
            setTimeout(() => this.connectWebSocket(), 3000);
        };
        
        this.ws.onerror = (error) => {
            console.error('WebSocket error:', error);
        };
    }
    
    handleWebSocketMessage(data) {
        switch (data.type) {
            case 'ack':
                this.conversationId = data.conversation_id;
                break;
                
            case 'sql':
                this.removeLoadingIndicator();
                this.lastGeneratedSql = data.content;
                const assistantMsg = { role: 'assistant', content: data.content, sql: data.content, timestamp: new Date().toISOString() };
                this.sessionMessages.push(assistantMsg);
                this.renderAssistantMessage(data.content, null, this.sessionMessages.length - 1);
                this.copyToClipboard(data.content);
                this.updatePromptsList(this.sessionMessages);
                break;
                
            case 'results':
                this.appendResultsToLastMessage(data);
                this.saveToCsv(data.content);
                if (this.sessionMessages.length > 0) {
                    const last = this.sessionMessages[this.sessionMessages.length - 1];
                    if (last.role === 'assistant') last.results = data.content;
                }
                break;
                
            case 'error':
                this.removeLoadingIndicator();
                if (data.content === 'QUOTA_EXCEEDED') {
                    showQuotaModal();
                } else {
                    this.renderErrorMessage(data.content);
                }
                break;
                
            case 'done':
                this.isLoading = false;
                this.updateSendButton();
                this.updatePromptsList(this.sessionMessages);
                this.loadConversations();
                break;
        }
    }
    
    async sendMessage() {
        // In playground mode, anonymous users are allowed (they signed in silently).
        // Re-check quota on every send so we block mid-session when limit is reached.
        if (_isPlaygroundActive && _currentUser?.isAnonymous) {
            const count = await checkPlaygroundQuota();
            if (count >= 20) {
                showQuotaModal();
                return;
            }
        } else if (!isAuthenticated()) {
            signIn();
            return;
        }
        const query = this.promptInput.value.trim();
        if (!query || this.isLoading) return;
        
        this.isLoading = true;
        this.updateSendButton();
        
        // Hide welcome message
        this.welcomeMessage.style.display = 'none';
        
        // Render user message
        const userMsg = { role: 'user', content: query, timestamp: new Date().toISOString() };
        this.sessionMessages.push(userMsg);
        this.renderMessage(userMsg, this.sessionMessages.length - 1);
        
        // Clear input and attached files
        this.promptInput.value = '';
        this.attachedFiles = [];
        this.renderAttachedFiles();
        this.autoResizeTextarea();
        
        // Show loading indicator
        this.showLoadingIndicator();
        
        // Send via WebSocket
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify({
                query: query,
                conversation_id: this.conversationId,
                execute: this.executeCheckbox.checked,
                is_playground: _isPlaygroundActive,
            }));
        } else {
            // Fallback to REST API
            await this.sendMessageRest(query);
        }
        
        this.scrollToBottom();
    }
    
    async sendMessageRest(query) {
        try {
            const response = await apiFetch('/api/query', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    query: query,
                    conversation_id: this.conversationId,
                    execute: this.executeCheckbox.checked,
                    is_playground: _isPlaygroundActive,
                })
            });

            // Handle quota-exceeded response from server.
            if (response.status === 403) {
                const err = await response.json().catch(() => ({}));
                this.removeLoadingIndicator();
                if (err.detail === 'QUOTA_EXCEEDED') {
                    showQuotaModal();
                    return;
                }
                throw new Error(`HTTP 403: ${err.detail || 'Forbidden'}`);
            }

            const data = await response.json();
            
            this.removeLoadingIndicator();
            this.conversationId = data.conversation_id;
            this.lastGeneratedSql = data.sql;
            
            const assistantMsg = { role: 'assistant', content: data.sql, sql: data.sql, results: data.results, timestamp: new Date().toISOString() };
            this.sessionMessages.push(assistantMsg);
            this.renderAssistantMessage(data.sql, data.results, this.sessionMessages.length - 1);
            this.copyToClipboard(data.sql);
            this.saveToCsv(data.results);
            this.updatePromptsList(this.sessionMessages);
            
            await this.loadConversations();
        } catch (error) {
            this.removeLoadingIndicator();
            this.renderErrorMessage(error.message);
        } finally {
            this.isLoading = false;
            this.updateSendButton();
        }
    }
    
    updatePromptsList(messages) {
        const section = document.getElementById('promptsSection');
        const list = document.getElementById('promptsList');
        if (!section || !list) return;
        
        const msgs = messages || [];
        const userIndices = msgs.map((m, i) => m.role === 'user' ? i : -1).filter(i => i >= 0);
        if (userIndices.length === 0) {
            section.style.display = 'none';
            return;
        }
        
        section.style.display = 'block';
        list.innerHTML = userIndices.map(idx => {
            const m = msgs[idx];
            const title = (m.content || '').slice(0, 50) + ((m.content || '').length > 50 ? '...' : '');
            return `<div class="prompt-item" data-msg-index="${idx}" title="${this.escapeHtml(m.content || '')}">${this.escapeHtml(title)}</div>`;
        }).join('');
        
        list.querySelectorAll('.prompt-item').forEach(el => {
            el.addEventListener('click', () => {
                const target = this.messagesContainer.querySelector(`[data-msg-index="${el.dataset.msgIndex}"]`);
                if (target) {
                    target.scrollIntoView({ behavior: 'smooth', block: 'center' });
                    list.querySelectorAll('.prompt-item').forEach(p => p.classList.remove('active'));
                    el.classList.add('active');
                }
            });
        });
    }
    
    renderMessage(msg, msgIndex = -1) {
        const messageEl = document.createElement('div');
        messageEl.className = `message ${msg.role}`;
        if (msgIndex >= 0) messageEl.dataset.msgIndex = msgIndex;
        
        const avatar = msg.role === 'user' ? 'U' : 'D';
        const roleName = msg.role === 'user' ? 'You' : 'DaiBai';
        const time = this.formatTime(msg.timestamp);
        
        if (msg.role === 'user') {
            messageEl.innerHTML = `
                <div class="message-avatar">${avatar}</div>
                <div class="message-content">
                    <div class="message-header">
                        <span class="message-role">${roleName}</span>
                        <span class="message-time">${time}</span>
                    </div>
                    <div class="message-text">${this.escapeHtml(msg.content)}</div>
                </div>
            `;
        } else {
            messageEl.innerHTML = `
                <div class="message-avatar">${avatar}</div>
                <div class="message-content">
                    <div class="message-header">
                        <span class="message-role">${roleName}</span>
                        <span class="message-time">${time}</span>
                    </div>
                    ${this.renderSqlBlock(msg.sql || msg.content)}
                    ${msg.results ? this.renderResults(msg.results) : ''}
                </div>
            `;
        }
        
        this.messagesContainer.appendChild(messageEl);
        if (msg.role === 'assistant' && msg.results) {
            this.bindExportCsvButtons(messageEl);
        }
        this.scrollToBottom();
    }
    
    renderAssistantMessage(sql, results = null, msgIndex = -1) {
        const messageEl = document.createElement('div');
        messageEl.className = 'message assistant';
        messageEl.id = 'lastAssistantMessage';
        if (msgIndex >= 0) messageEl.dataset.msgIndex = msgIndex;
        
        messageEl.innerHTML = `
            <div class="message-avatar">D</div>
            <div class="message-content">
                <div class="message-header">
                    <span class="message-role">DaiBai</span>
                    <span class="message-time">${this.formatTime(new Date().toISOString())}</span>
                </div>
                ${this.renderSqlBlock(sql)}
                ${results ? this.renderResults(results) : ''}
            </div>
        `;
        
        this.messagesContainer.appendChild(messageEl);
        this.scrollToBottom();
        
        // Bind copy, run, and export buttons
        this.bindSqlActions(messageEl, sql);
        this.bindExportCsvButtons(messageEl);
    }
    
    renderSqlBlock(sql) {
        if (!sql) return '<div class="message-text">Could not generate SQL</div>';
        
        return `
            <div class="sql-block">
                <div class="sql-header">
                    <span>SQL</span>
                    <div class="sql-actions">
                        <button class="copy-btn">Copy</button>
                        <button class="run-btn">Run</button>
                    </div>
                </div>
                <pre class="sql-code">${this.escapeHtml(sql)}</pre>
            </div>
        `;
    }
    
    renderResults(results) {
        if (!results || results.length === 0) {
            return '<div class="results-container"><p>No results</p></div>';
        }
        
        const columns = Object.keys(results[0]);
        const resultsId = 'results-' + Date.now() + '-' + Math.random().toString(36).slice(2);
        this.resultsCache[resultsId] = results;
        
        return `
            <div class="results-container" data-results-id="${resultsId}">
                <div class="results-header">
                    <span>${results.length} row(s) returned</span>
                    <button class="export-csv-btn" data-results-id="${resultsId}" title="Export to CSV">Export CSV</button>
                </div>
                <table class="results-table">
                    <thead>
                        <tr>${columns.map(col => `<th>${this.escapeHtml(col)}</th>`).join('')}</tr>
                    </thead>
                    <tbody>
                        ${results.slice(0, 100).map(row => `
                            <tr>${columns.map(col => `<td>${this.escapeHtml(String(row[col] ?? ''))}</td>`).join('')}</tr>
                        `).join('')}
                    </tbody>
                </table>
                ${results.length > 100 ? `<p style="margin-top: 8px; color: var(--text-muted);">Showing first 100 of ${results.length} rows</p>` : ''}
            </div>
        `;
    }
    
    appendResultsToLastMessage(data) {
        const lastMessage = document.getElementById('lastAssistantMessage');
        if (lastMessage) {
            const content = lastMessage.querySelector('.message-content');
            const resultsHtml = this.renderResults(data.content);
            content.insertAdjacentHTML('beforeend', resultsHtml);
            this.bindExportCsvButtons(lastMessage);
            this.scrollToBottom();
        }
    }
    
    bindExportCsvButtons(container) {
        (container || this.messagesContainer).querySelectorAll('.export-csv-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                const resultsId = btn.dataset.resultsId;
                const results = this.resultsCache[resultsId];
                if (results && results.length > 0) {
                    this.saveToCsv(results);
                    btn.textContent = 'Saved!';
                    setTimeout(() => { btn.textContent = 'Export CSV'; }, 2000);
                }
            });
        });
    }
    
    bindSqlActions(messageEl, sql) {
        const copyBtn = messageEl.querySelector('.copy-btn');
        const runBtn = messageEl.querySelector('.run-btn');
        
        if (copyBtn) {
            copyBtn.addEventListener('click', () => {
                navigator.clipboard.writeText(sql);
                copyBtn.textContent = 'Copied!';
                setTimeout(() => copyBtn.textContent = 'Copy', 2000);
            });
        }
        
        if (runBtn) {
            runBtn.addEventListener('click', async () => {
                runBtn.textContent = 'Running...';
                runBtn.disabled = true;
                
                try {
                    const response = await apiFetch('/api/execute', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ sql: sql })
                    });
                    
                    const data = await response.json();
                    
                    if (response.ok) {
                        this.appendResultsToLastMessage({ content: data.results });
                        this.saveToCsv(data.results);
                    } else {
                        this.renderErrorMessage(data.detail || 'Execution failed');
                    }
                } catch (error) {
                    this.renderErrorMessage(error.message);
                } finally {
                    runBtn.textContent = 'Run';
                    runBtn.disabled = false;
                }
            });
        }
    }
    
    renderErrorMessage(error) {
        const messageEl = document.createElement('div');
        messageEl.className = 'message assistant';
        
        // Extract error message from various formats
        let errorMsg = 'Unknown error';
        if (typeof error === 'string') {
            errorMsg = error;
        } else if (error && error.message) {
            errorMsg = error.message;
        } else if (error && error.detail) {
            errorMsg = error.detail;
        } else if (error && typeof error === 'object') {
            errorMsg = JSON.stringify(error);
        }
        
        messageEl.innerHTML = `
            <div class="message-avatar" style="background: var(--error);">!</div>
            <div class="message-content">
                <div class="message-header">
                    <span class="message-role">Error</span>
                </div>
                <div class="message-text" style="color: var(--error);">${this.escapeHtml(errorMsg)}</div>
            </div>
        `;
        
        this.messagesContainer.appendChild(messageEl);
        this.scrollToBottom();
    }
    
    showLoadingIndicator() {
        const loadingEl = document.createElement('div');
        loadingEl.className = 'message assistant loading-message';
        loadingEl.innerHTML = `
            <div class="message-avatar">D</div>
            <div class="loading">
                <div class="loading-dots">
                    <span></span>
                    <span></span>
                    <span></span>
                </div>
                <span>Generating...</span>
            </div>
        `;
        this.messagesContainer.appendChild(loadingEl);
        this.scrollToBottom();
    }
    
    removeLoadingIndicator() {
        const loading = this.messagesContainer.querySelector('.loading-message');
        if (loading) {
            loading.remove();
        }
    }
    
    async showSchema() {
        this.schemaModal.classList.add('active');
        this.schemaContent.textContent = 'Loading schema...';
        
        try {
            const response = await apiFetch('/api/schema');
            const data = await response.json();
            this.schemaContent.textContent = data.schema || 'No schema available';
        } catch (error) {
            this.schemaContent.textContent = 'Failed to load schema: ' + error.message;
        }
    }

    async showSettings() {
        if (!this.settingsModal) return;
        this.settingsActiveTab = this.settingsActiveTab || 'account';
        try {
            this.settingsState = await this.loadSettingsState();
        } catch (e) {
            this.settingsState = {};
        }
        this.settingsModal.classList.add('active');
        this.renderSettingsContent(this.settingsActiveTab);
        this.settingsModal.querySelectorAll('.settings-tab').forEach(t => {
            t.classList.toggle('active', t.dataset.tab === this.settingsActiveTab);
        });
    }

    async loadSettingsState() {
        try {
            const [settingsRes, prefs] = await Promise.all([
                apiFetch('/api/settings'),
                Promise.resolve(JSON.parse(localStorage.getItem('daibai_preferences') || '{}'))
            ]);
            const settings = await settingsRes.json();
            const configured = settings.llm_providers || [];
            const apiConfigs = settings.llm_provider_configs || {};
            const llm_providers = {};
            for (const p of SUPPORTED_LLM_PROVIDERS) {
                const c = apiConfigs[p];
                llm_providers[p] = c ? { api_key: c.api_key, model: c.model, endpoint: c.endpoint, deployment: c.deployment } : {};
            }
            return {
                account: { email: '', user_id: '', plan: 'Free' },
                llm: { provider: settings.current_llm || 'gemini' },
                configured_llm_providers: configured,
                llm_providers,
                selected_llm_provider: configured[0] || SUPPORTED_LLM_PROVIDERS[0],
                databases: { type: 'mysql', hostType: 'local', current: settings.current_database },
                data_privacy: { save_history: true, query_caching: false },
                preferences: { theme: 'system', auto_charts: false, ...prefs }
            };
        } catch (e) {
            return {};
        }
    }

    closeSettings() {
        this.settingsModal.classList.remove('active');
    }

    switchSettingsTab(tabId) {
        this.settingsActiveTab = tabId;
        this.settingsModal.querySelectorAll('.settings-tab').forEach(t => {
            t.classList.toggle('active', t.dataset.tab === tabId);
        });
        this.renderSettingsContent(tabId);
    }

    renderSettingsContent(tabId) {
        const content = this.settingsContent;
        switch (tabId) {
            case 'account':
                content.innerHTML = this.renderAccountTab();
                break;
            case 'llm_providers':
                content.innerHTML = this.renderLLMProvidersTab();
                break;
            case 'databases':
                content.innerHTML = this.renderDatabaseConnectionsTab();
                break;
            case 'data':
                content.innerHTML = this.renderDataPrivacyTab();
                break;
            case 'preferences':
                content.innerHTML = this.renderPreferencesTab();
                break;
            default:
                content.innerHTML = '';
        }
        this.bindSettingsDynamicHandlers();
    }

    renderAccountTab() {
        const auth = this.settingsState?.account || {};
        return `
            <div class="settings-group">
                <div class="settings-group-title">User Identity</div>
                <div class="settings-field">
                    <label>Email</label>
                    <input type="text" id="settingsEmail" value="${this.escapeHtml(auth.email || '')}" placeholder="Not signed in" readonly>
                </div>
                <div class="settings-field">
                    <label>User ID</label>
                    <input type="text" id="settingsUserId" value="${this.escapeHtml(auth.user_id || '')}" placeholder="—" readonly>
                </div>
            </div>
            <div class="settings-group">
                <div class="settings-group-title">Plan Management</div>
                <div class="settings-field">
                    <label>Current Plan</label>
                    <input type="text" id="settingsPlan" value="${this.escapeHtml(auth.plan || 'Free')}" readonly>
                </div>
            </div>
            <div class="settings-group">
                <div class="settings-group-title">Billing</div>
                <button class="btn-secondary" id="settingsManageSubscription">Manage Subscription</button>
            </div>
        `;
    }

    renderLLMProvidersTab() {
        const configured = this.settingsState?.configured_llm_providers || [];
        const providers = this.settingsState?.llm_providers || {};
        const selected = this.settingsState?.selected_llm_provider || (configured[0] || SUPPORTED_LLM_PROVIDERS[0]);
        const navItems = SUPPORTED_LLM_PROVIDERS.map(p => {
            const isPopulated = configured.includes(p);
            const isActive = p === selected;
            const label = (LLM_TEMPLATES[p] || {}).label || p.charAt(0).toUpperCase() + p.slice(1);
            return `<button type="button" class="settings-nav-item ${isActive ? 'active' : ''}" data-provider="${p}">
                <span class="status-dot ${isPopulated ? 'populated' : 'empty'}">${isPopulated ? '●' : '○'}</span>
                <span>${this.escapeHtml(label)}</span>
            </button>`;
        }).join('');
        const formHtml = this.renderLLMProviderForm(selected, providers[selected] || {});
        return `
            <div class="settings-split">
                <nav class="settings-nav">
                    <div class="settings-group-title" style="padding:0 16px 8px;margin-bottom:0">Provider</div>
                    ${navItems}
                </nav>
                <div class="settings-main" id="settingsLLMMain">
                    ${formHtml}
                </div>
            </div>
        `;
    }

    renderLLMProviderForm(provider, values = {}) {
        const t = LLM_TEMPLATES[provider] || LLM_TEMPLATES.gemini;
        const label = t.label || provider.charAt(0).toUpperCase() + provider.slice(1);
        let connectivityHtml = '';
        if (t.endpointDefault !== undefined) {
            connectivityHtml += `<div class="settings-field"><label>Endpoint URL</label><input type="url" id="settingsLLMEndpoint" value="${this.escapeHtml(values.endpoint || t.endpointDefault)}"></div>`;
        }
        if (t.needsApiKey) {
            connectivityHtml += `<div class="settings-field"><label>API Key</label><input type="password" id="settingsLLMApiKey" value="${this.escapeHtml(values.api_key || '')}" placeholder="••••••••"></div>`;
        }
        if (t.fields.includes('deployment')) {
            connectivityHtml += `<div class="settings-field"><label>Deployment</label><input type="text" id="settingsLLMDeployment" value="${this.escapeHtml(values.deployment || '')}" placeholder="deployment name"></div>`;
        }
        const modelSection = t.fields.includes('model') ? `
            <div class="settings-group">
                <div class="settings-group-title">Model & Behavior</div>
                <div class="settings-field">
                    <label>Default Model</label>
                    <div class="model-fetch-container">
                        <input type="text" id="settingsLLMModel" list="settingsLLMModelList" value="${this.escapeHtml(values.model || '')}" placeholder="e.g. gpt-4o, gemini-2.5-pro">
                        <datalist id="settingsLLMModelList"></datalist>
                        <button type="button" class="btn-secondary btn-fetch-models" id="settingsLLMGetModels" title="Fetch available models from provider">Get Models</button>
                    </div>
                </div>
            </div>
        ` : '';
        return `
            <input type="hidden" id="settingsLLMProvider" value="${this.escapeHtml(provider)}">
            <div class="settings-group">
                <div class="settings-group-title">Connectivity</div>
                ${connectivityHtml}
            </div>
            ${modelSection}
            <button type="button" class="btn-secondary" id="settingsLLMTest">Test Connection</button>
        `;
    }

    renderDatabaseConnectionsTab() {
        const db = this.settingsState?.databases || { type: 'mysql', hostType: 'local' };
        const dbOptions = ['mysql', 'postgres', 'oracle', 'sqlserver'];
        const dbHtml = this.renderDBTemplate(db.type || 'mysql', db.hostType || 'local', db.cloudProvider || 'aws', db);
        return `
            <div class="settings-group">
                <div class="settings-group-title">Database Type</div>
                <div class="settings-field">
                    <label>Type</label>
                    <select id="settingsDBType">
                        ${dbOptions.map(d => `<option value="${d}" ${(db.type || 'mysql') === d ? 'selected' : ''}>${d.charAt(0).toUpperCase() + d.slice(1)}</option>`).join('')}
                    </select>
                </div>
                <div class="settings-field">
                    <label>Host Type</label>
                    <select id="settingsHostType">
                        <option value="local" ${(db.hostType || 'local') === 'local' ? 'selected' : ''}>Local</option>
                        <option value="cloud" ${(db.hostType || 'local') === 'cloud' ? 'selected' : ''}>Cloud</option>
                    </select>
                </div>
                <div id="settingsDBCloudProvider" style="${(db.hostType || 'local') === 'cloud' ? '' : 'display:none'}">
                    <div class="settings-field">
                        <label>Cloud Provider</label>
                        <select id="settingsCloudProvider">
                            <option value="aws" ${(db.cloudProvider || 'aws') === 'aws' ? 'selected' : ''}>AWS</option>
                            <option value="azure" ${(db.cloudProvider || 'aws') === 'azure' ? 'selected' : ''}>Azure</option>
                        </select>
                    </div>
                </div>
                <div id="settingsDBDynamicFields">${dbHtml}</div>
            </div>
        `;
    }

    renderLLMTemplate(provider, values = {}) {
        const t = LLM_TEMPLATES[provider] || LLM_TEMPLATES.gemini;
        let html = '';
        if (t.endpointDefault !== undefined) {
            html += `<div class="settings-field"><label>Endpoint URL</label><input type="url" id="settingsLLMEndpoint" value="${this.escapeHtml(values.endpoint || t.endpointDefault)}"></div>`;
        }
        if (t.needsApiKey) {
            html += `<div class="settings-field"><label>API Key</label><input type="password" id="settingsLLMApiKey" value="${this.escapeHtml(values.api_key || '')}" placeholder="••••••••"></div>`;
            html += `<div class="settings-field"><label>Model Version</label><input type="text" id="settingsLLMModel" value="${this.escapeHtml(values.model || '')}" placeholder="e.g. gpt-4o"></div>`;
        }
        return html || '';
    }

    renderDBTemplate(dbType, hostType, cloudProvider, values = {}) {
        const t = DB_TEMPLATES[dbType] || DB_TEMPLATES.mysql;
        const port = values.port || t.defaultPort || 3306;
        let html = '';
        if (hostType === 'cloud') {
            const cloud = CLOUD_PROVIDERS[cloudProvider] || CLOUD_PROVIDERS.aws;
            cloud.fields.forEach(f => {
                const val = values[f] || '';
                html += `<div class="settings-field"><label>${f.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase())}</label><input type="text" id="settingsDB${f}" value="${this.escapeHtml(val)}"></div>`;
            });
        } else {
            t.fields.forEach(f => {
                const val = values[f] || (f === 'port' ? port : '');
                const inputType = f === 'password' ? 'password' : 'text';
                html += `<div class="settings-field"><label>${f.charAt(0).toUpperCase() + f.slice(1).replace(/_/g, ' ')}</label><input type="${inputType}" id="settingsDB${f}" value="${this.escapeHtml(val)}" ${f === 'port' ? `placeholder="${port}"` : ''}></div>`;
            });
        }
        return html;
    }

    renderDataPrivacyTab() {
        const data = this.settingsState?.data_privacy || {};
        return `
            <div class="settings-group">
                <div class="settings-group-title">History Control</div>
                <div class="settings-toggle">
                    <label>Save Session History</label>
                    <input type="checkbox" id="settingsSaveHistory" ${(data.save_history !== false) ? 'checked' : ''}>
                </div>
                <button class="btn-danger" id="settingsClearConversations">Clear All Conversations</button>
            </div>
            <div class="settings-group">
                <div class="settings-group-title">RAG & Caching</div>
                <div class="settings-toggle">
                    <label>Semantic Query Caching</label>
                    <input type="checkbox" id="settingsQueryCaching" ${(data.query_caching === true) ? 'checked' : ''}>
                </div>
                <button class="btn-secondary" id="settingsIndexRefresh" style="margin-top:12px">Index Refresh</button>
            </div>
        `;
    }

    renderPreferencesTab() {
        const prefs = this.settingsState?.preferences || {};
        return `
            <div class="settings-group">
                <div class="settings-group-title">Appearance</div>
                <div class="settings-field">
                    <label>Theme</label>
                    <select id="settingsTheme">
                        <option value="light" ${(prefs.theme || 'system') === 'light' ? 'selected' : ''}>Light</option>
                        <option value="dark" ${(prefs.theme || 'system') === 'dark' ? 'selected' : ''}>Dark</option>
                        <option value="system" ${(prefs.theme || 'system') === 'system' ? 'selected' : ''}>System</option>
                    </select>
                </div>
            </div>
            <div class="settings-group">
                <div class="settings-group-title">Output Default</div>
                <div class="settings-toggle">
                    <label>Auto-generate Charts</label>
                    <input type="checkbox" id="settingsAutoCharts" ${(prefs.auto_charts === true) ? 'checked' : ''}>
                </div>
                <div class="settings-field" style="margin-top:8px">
                    <span class="hint">When unchecked, output is raw data only</span>
                </div>
            </div>
        `;
    }

    readLLMFormValues() {
        const getVal = id => document.getElementById(id)?.value ?? '';
        return {
            endpoint: getVal('settingsLLMEndpoint'),
            api_key: getVal('settingsLLMApiKey'),
            model: getVal('settingsLLMModel'),
            deployment: getVal('settingsLLMDeployment')
        };
    }

    bindSettingsDynamicHandlers() {
        const testBtn = document.getElementById('settingsLLMTest');
        if (testBtn) {
            testBtn.onclick = () => this.testLLMConnection();
        }
        const getModelsBtn = document.getElementById('settingsLLMGetModels');
        if (getModelsBtn) {
            getModelsBtn.onclick = () => this.fetchAvailableModels();
        }
        const dbTypeSelect = document.getElementById('settingsDBType');
        const hostTypeSelect = document.getElementById('settingsDBHostType') || document.getElementById('settingsHostType');
        const cloudProviderSelect = document.getElementById('settingsCloudProvider');
        const cloudDiv = document.getElementById('settingsDBCloudProvider');
        const dbFieldsDiv = document.getElementById('settingsDBDynamicFields');
        if (hostTypeSelect) {
            hostTypeSelect.onchange = () => {
                const isCloud = hostTypeSelect.value === 'cloud';
                if (cloudDiv) cloudDiv.style.display = isCloud ? '' : 'none';
                if (dbFieldsDiv) dbFieldsDiv.innerHTML = this.renderDBTemplate(dbTypeSelect?.value || 'mysql', hostTypeSelect.value, cloudProviderSelect?.value || 'aws', {});
            };
        }
        if (dbTypeSelect && dbFieldsDiv) {
            dbTypeSelect.onchange = () => {
                dbFieldsDiv.innerHTML = this.renderDBTemplate(dbTypeSelect.value, hostTypeSelect?.value || 'local', cloudProviderSelect?.value || 'aws', {});
            };
        }
        if (cloudProviderSelect && dbFieldsDiv) {
            cloudProviderSelect.onchange = () => {
                dbFieldsDiv.innerHTML = this.renderDBTemplate(dbTypeSelect?.value || 'mysql', 'cloud', cloudProviderSelect.value, {});
            };
        }
        const clearBtn = document.getElementById('settingsClearConversations');
        if (clearBtn) {
            clearBtn.onclick = () => this.clearAllConversations();
        }
        const indexBtn = document.getElementById('settingsIndexRefresh');
        if (indexBtn) {
            indexBtn.onclick = () => this.refreshIndex();
        }
        const manageBtn = document.getElementById('settingsManageSubscription');
        if (manageBtn) {
            manageBtn.onclick = () => this.openStripePortal();
        }
    }

    async clearAllConversations() {
        if (!confirm('Clear all conversations? This cannot be undone.')) return;
        try {
            const response = await apiFetch('/api/conversations');
            const conversations = await response.json();
            for (const c of conversations) {
                await apiFetch(`/api/conversations/${c.id}`, { method: 'DELETE' });
            }
            this.startNewChat();
            await this.loadConversations();
            this.closeSettings();
        } catch (e) {
            console.error('Failed to clear conversations:', e);
        }
    }

    async refreshIndex() {
        try {
            await apiFetch('/api/schema'); // Triggers schema load; backend could add /api/refresh-index
            alert('Index refresh requested.');
        } catch (e) {
            console.error('Failed to refresh index:', e);
        }
    }

    openStripePortal() {
        // TODO: Fetch Stripe portal URL from backend when implemented
        window.open('#', '_blank');
    }

    async testLLMConnection() {
        const provider = this.settingsState?.selected_llm_provider || document.getElementById('settingsLLMProvider')?.value;
        const values = this.readLLMFormValues();
        try {
            const res = await apiFetch('/api/test-llm', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ provider, ...values })
            });
            if (res.ok) {
                const data = await res.json();
                alert(data.success ? 'Connection successful' : (data.error || 'Connection failed'));
            } else {
                alert('Connection test failed');
            }
        } catch (e) {
            alert('Connection test failed: ' + (e.message || 'Network error'));
        }
    }

    async fetchAvailableModels() {
        const btn = document.getElementById('settingsLLMGetModels');
        const provider = this.settingsState?.selected_llm_provider || document.getElementById('settingsLLMProvider')?.value;
        const values = this.readLLMFormValues();
        let apiKey = values.api_key || '';
        // Don't send masked placeholder - backend will use config/env
        const MASKED = ['••••••', '••••••••', '********'];
        if (MASKED.includes(apiKey) || /^[•\u2022*]+$/.test(apiKey)) {
            apiKey = null;
        }
        const baseUrl = values.endpoint || '';

        if (btn) {
            btn.disabled = true;
            btn.textContent = 'Fetching...';
        }
        try {
            const res = await apiFetch('/api/config/fetch-models', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    provider,
                    api_key: apiKey || null,
                    base_url: baseUrl || null
                })
            });
            const data = await res.json();
            const datalist = document.getElementById('settingsLLMModelList');
            if (datalist) {
                datalist.innerHTML = '';
                if (data.models && data.models.length > 0) {
                    data.models.forEach(m => {
                        const opt = document.createElement('option');
                        opt.value = m;
                        datalist.appendChild(opt);
                    });
                }
            }
            if (data.error) {
                alert(data.error);
            } else if (data.message && !data.models?.length) {
                alert(data.message);
            }
        } catch (e) {
            alert('Failed to fetch models: ' + (e.message || 'Network error'));
        } finally {
            if (btn) {
                btn.disabled = false;
                btn.textContent = 'Get Models';
            }
        }
    }

    buildConfigPayload() {
        const getVal = id => document.getElementById(id)?.value ?? '';
        const account = {
            email: getVal('settingsEmail'),
            user_id: getVal('settingsUserId'),
            plan: getVal('settingsPlan') || 'Free'
        };
        const selected = this.settingsState?.selected_llm_provider || 'gemini';
        if (selected) {
            this.settingsState.llm_providers = this.settingsState.llm_providers || {};
            this.settingsState.llm_providers[selected] = this.readLLMFormValues();
        }
        const llm = { provider: selected };
        const llm_providers = this.settingsState?.llm_providers || {};
        const dbType = getVal('settingsDBType') || 'mysql';
        const hostType = getVal('settingsHostType') || 'local';
        const databases = {
            type: dbType,
            hostType: hostType,
            cloudProvider: getVal('settingsCloudProvider') || 'aws',
            host: getVal('settingsDBhost'),
            port: getVal('settingsDBport'),
            user: getVal('settingsDBuser'),
            password: getVal('settingsDBpassword'),
            database: getVal('settingsDBdatabase'),
            service_name: getVal('settingsDBservice_name'),
            region: getVal('settingsDBregion'),
            secret_arn: getVal('settingsDBsecret_arn'),
            instance: getVal('settingsDBinstance')
        };
        const data_privacy = {
            save_history: document.getElementById('settingsSaveHistory')?.checked ?? true,
            query_caching: document.getElementById('settingsQueryCaching')?.checked ?? false
        };
        const preferences = {
            theme: getVal('settingsTheme') || 'system',
            auto_charts: document.getElementById('settingsAutoCharts')?.checked ?? false
        };
        return { account, llm, llm_providers, databases, data_privacy, preferences };
    }

    async saveSettings() {
        const payload = this.buildConfigPayload();
        try {
            const response = await apiFetch('/api/config', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            if (response.ok) {
                this.settingsState = {
                    ...payload,
                    configured_llm_providers: Object.keys(payload.llm_providers || {}).filter(p => {
                        const v = payload.llm_providers[p];
                        return v && (v.api_key || v.endpoint || v.model);
                    }),
                    selected_llm_provider: payload.llm?.provider || this.settingsState?.selected_llm_provider
                };
                this.closeSettings();
                await this.loadSettings();
            } else {
                console.error('Failed to save settings');
            }
        } catch (e) {
            console.error('Failed to save settings:', e);
        }
    }
    
    scrollToBottom() {
        this.messagesContainer.scrollTop = this.messagesContainer.scrollHeight;
    }
    
    formatTime(timestamp) {
        if (!timestamp) return '';
        const date = new Date(timestamp);
        return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    }
    
    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
}

document.addEventListener('DOMContentLoaded', () => {
    // Replace all <i data-lucide="..."> elements with inline SVGs.
    if (typeof lucide !== 'undefined') {
        lucide.createIcons();
    } else {
        console.warn('[Lucide] Library not loaded — icons will appear as empty elements. Check CDN.');
    }

    // Auth modal: close button + backdrop.
    document.getElementById('authModalClose')?.addEventListener('click', () => {
        document.getElementById('authModal')?.classList.remove('active');
    });
    document.getElementById('authModal')?.addEventListener('click', (e) => {
        if (e.target === document.getElementById('authModal')) {
            e.target.classList.remove('active');
        }
    });

    // Profile modal: close button + backdrop.
    document.getElementById('profileModalClose')?.addEventListener('click', () => {
        document.getElementById('profileModal')?.classList.remove('active');
    });
    document.getElementById('profileModal')?.addEventListener('click', (e) => {
        if (e.target === document.getElementById('profileModal')) {
            e.target.classList.remove('active');
        }
    });

    // Guest: Sign In dropdown item.
    document.getElementById('dropdownSignIn')?.addEventListener('click', () => {
        document.getElementById('profileDropdown')?.classList.remove('open');
        document.getElementById('profileAvatarBtn')?.setAttribute('aria-expanded', 'false');
        signIn();
    });

    // Helper: close the profile dropdown
    const closeProfileDropdown = () => {
        document.getElementById('profileDropdown')?.classList.remove('open');
        document.getElementById('profileAvatarBtn')?.setAttribute('aria-expanded', 'false');
    };

    // Edit Profile dropdown item.
    document.getElementById('dropdownEditProfile')?.addEventListener('click', () => {
        closeProfileDropdown();
        openProfileModal();
    });

    // Database Schema shortcut from dropdown.
    document.getElementById('dropdownSchema')?.addEventListener('click', () => {
        closeProfileDropdown();
        window.app?.showSchema();
    });

    // Documentation — opens in a new tab.
    document.getElementById('dropdownDocs')?.addEventListener('click', () => {
        closeProfileDropdown();
        window.open('https://github.com/your-org/daibai#readme', '_blank', 'noopener');
    });

    // Help & Support.
    document.getElementById('dropdownHelp')?.addEventListener('click', () => {
        closeProfileDropdown();
        window.open('https://github.com/your-org/daibai/issues', '_blank', 'noopener');
    });

    // Keyboard shortcuts modal.
    document.getElementById('dropdownKeyboard')?.addEventListener('click', () => {
        closeProfileDropdown();
        document.getElementById('keyboardModal')?.classList.add('active');
    });
    document.getElementById('keyboardModalClose')?.addEventListener('click', () => {
        document.getElementById('keyboardModal')?.classList.remove('active');
    });
    document.getElementById('keyboardModal')?.addEventListener('click', (e) => {
        if (e.target === document.getElementById('keyboardModal')) {
            e.target.classList.remove('active');
        }
    });

    // ── Playground sidebar ──────────────────────────────────────────────────

    // Toggle the Playground submenu open/closed.
    document.getElementById('playgroundToggle')?.addEventListener('click', () => {
        const btn     = document.getElementById('playgroundToggle');
        const submenu = document.getElementById('playgroundSubmenu');
        const isOpen  = btn.getAttribute('aria-expanded') === 'true';
        btn.setAttribute('aria-expanded', String(!isOpen));
        btn.classList.toggle('open', !isOpen);
        submenu.classList.toggle('open', !isOpen);
        submenu.setAttribute('aria-hidden', String(isOpen));
        if (typeof lucide !== 'undefined') lucide.createIcons();
    });

    // "Query Chinook DB" — activate playground mode (sign in anonymously if needed).
    document.getElementById('queryChinookBtn')?.addEventListener('click', async () => {
        const sidebar  = document.getElementById('sidebar');
        const btn      = document.getElementById('queryChinookBtn');
        const turningOn = !btn.classList.contains('active');

        if (turningOn) {
            // Ensure a Firebase session exists (anonymous is fine for guests).
            if (!_currentUser) {
                try {
                    console.log('[Playground] No session — signing in anonymously…');
                    const cred = await firebase.auth().signInAnonymously();
                    _currentUser = cred.user;
                    console.log('[Playground] Anonymous UID:', cred.user.uid);
                } catch (e) {
                    console.error('[Playground] Anonymous sign-in failed:', e);
                    showSandboxStatusToast('error', 'Could not start a guest session — please sign in.');
                    return;
                }
            }

            // Check quota for anonymous (guest) users.
            if (_currentUser.isAnonymous) {
                const count = await checkPlaygroundQuota();
                console.log(`[Playground] Guest quota: ${count}/20`);
                if (count >= 20) {
                    showQuotaModal();
                    return;   // do not activate playground mode
                }
            }
        }

        btn.classList.toggle('active', turningOn);
        sidebar?.classList.toggle('active-playground', turningOn);
        window.app?.setPlaygroundMode?.(turningOn);
        console.log('[Playground] mode:', turningOn ? 'ON' : 'OFF');
    });

    // "Reset Sandbox" — show confirmation toast.
    document.getElementById('resetSandboxBtn')?.addEventListener('click', showSandboxConfirmToast);
    document.getElementById('sandboxCancelBtn')?.addEventListener('click', hideSandboxConfirmToast);
    document.getElementById('sandboxToastClose')?.addEventListener('click', hideSandboxConfirmToast);
    document.getElementById('sandboxConfirmBtn')?.addEventListener('click', executePlaygroundReset);

    // Quota modal buttons.
    document.getElementById('quotaSignupBtn')?.addEventListener('click', () => {
        hideQuotaModal();
        signIn();
    });
    document.getElementById('quotaReturnBtn')?.addEventListener('click', exitPlaygroundFromQuota);

    // Verification modal buttons.
    document.getElementById('checkVerifiedBtn')?.addEventListener('click', checkEmailVerified);
    document.getElementById('resendVerificationBtn')?.addEventListener('click', resendVerificationEmail);
    document.getElementById('verificationCancelBtn')?.addEventListener('click', cancelVerification);

    // Profile modal action buttons.
    document.getElementById('saveDisplayNameBtn')?.addEventListener('click', updateDisplayName);
    document.getElementById('sendSmsBtn')?.addEventListener('click', initPhoneVerification);
    document.getElementById('verifySmsBtn')?.addEventListener('click', verifyPhoneCode);

    // Allow pressing Enter in the SMS code field to confirm.
    document.getElementById('smsCodeInput')?.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') verifyPhoneCode();
    });

    // onAuthStateChanged is the single source of truth for session state.
    firebase.auth().onAuthStateChanged(async (user) => {
        const wasGuest = !_currentUser;

        if (user && !user.isAnonymous) {
            console.group('[AUTH] Session event');
            console.log('  uid          :', user.uid);
            console.log('  email        :', user.email || '(none)');
            console.log('  emailVerified:', user.emailVerified);
            console.log('  displayName  :', user.displayName || '(not set)');
            console.log('  provider     :', user.providerData.map(p => p.providerId).join(', '));
            console.log('  token exp    :', user.stsTokenManager?.expirationTime
                ? new Date(user.stsTokenManager.expirationTime).toLocaleString()
                : 'unknown');
            console.groupEnd();

            // Block email/password users who have not yet clicked the verification link.
            // _pendingVerificationUser keeps the Firebase session alive so reload() and
            // sendEmailVerification() work from the modal — we just don't grant app access.
            if (_requiresEmailVerification(user)) {
                console.log('[AUTH] Email not verified — blocking access, showing verification modal');
                _pendingVerificationUser = user;
                _currentUser = null;   // app stays in guest mode
                document.getElementById('authModal')?.classList.remove('active');
                _showVerificationModal(user.email);
                updateAuthButtons();
                return;                // skip onboarding and exitGuestMode
            }

            // Verified (or OAuth) user — grant full access.
            _currentUser = user;
            await onboardUser(user);
            if (wasGuest && window.app) {
                window.app.guestMode = false;
                await window.app.exitGuestMode();
            }

        } else if (user?.isAnonymous) {
            _currentUser = user;

        } else {
            // No session at all.
            const fbKeys = Object.keys(localStorage).filter(k => k.startsWith('firebase:'));
            console.group('[AUTH] No session — guest mode');
            console.log('  firebase localStorage keys:',
                fbKeys.length ? fbKeys : '(none — session was never saved or was cleared)');
            console.groupEnd();
            _currentUser = null;
        }

        updateAuthButtons();
    });

    window.app = new DaiBaiApp();
});