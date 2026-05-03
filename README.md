# 💸 Snappx Backend

<a name="readme-top"></a>

> The Django REST API powering Snappx, a Ghanaian susu savings platform. Handles group savings cycles, MoMo payouts, wallet ledgering, OTP verification, AI chat, and scheduled financial tasks.

## 📗 Table of Contents

- [About](#about)
  - [Built With](#built-with)
  - [Key Features](#key-features)
- [Frontend](#frontend)
- [Getting Started](#getting-started)
  - [Prerequisites](#prerequisites)
  - [Setup](#setup)
  - [Environment Variables](#environment-variables)
  - [Run Locally](#run-locally)
- [API Endpoints](#api-endpoints)
- [Running Tests](#running-tests)
- [Authors](#authors)
- [Future Features](#future-features)
- [Contributing](#contributing)
- [License](#license)

---

## 📖 About <a name="about"></a>

Snappx is a platform built for everyday Ghanaians. Users can join susu (group savings) circles, set personal savings goals, top up their wallets via Paystack, and receive rotational MoMo payouts automatically. This repository contains the Django backend that powers the entire platform, from financial ledgering to AI-assisted savings advice.

### 🛠 Built With <a name="built-with"></a>

<details>
  <summary>Backend</summary>
  <ul>
    <li><a href="https://www.djangoproject.com/">Django</a></li>
    <li><a href="https://www.django-rest-framework.org/">Django REST Framework</a></li>
    <li><a href="https://django-rest-framework-simplejwt.readthedocs.io/">Simple JWT</a></li>
    <li><a href="https://www.postgresql.org/">PostgreSQL (Neon)</a></li>
    <li><a href="https://redis.io/">Redis (Upstash)</a></li>
    <li><a href="https://upstash.com/docs/qstash">QStash (django-qstash)</a></li>
    <li><a href="https://cloudinary.com/">Cloudinary</a></li>
    <li><a href="https://paystack.com/">Paystack</a></li>
    <li><a href="https://groq.com/">Groq (LLaMA 3.3 70B)</a></li>
  </ul>
</details>

<details>
  <summary>Deployment</summary>
  <ul>
    <li><a href="https://vercel.com/">Vercel</a> — Serverless Django via WSGI</li>
    <li><a href="https://neon.tech/">Neon</a> — Serverless PostgreSQL</li>
    <li><a href="https://upstash.com/">Upstash</a> — Redis cache + QStash task queue</li>
  </ul>
</details>

<p align="right">(<a href="#readme-top">back to top</a>)</p>

### ✨ Key Features <a name="key-features"></a>

- **Susu group savings** - rotational payout cycles with automatic MoMo disbursements via Paystack
- **Double-entry ledger** - immutable `LedgerEntry` records as the single source of truth for all balances
- **MoMo phone login** - users log in with their mobile money number (stored hashed + encrypted)
- **OTP verification** - phone number verification via Dawurobo SMS with brute-force lockout
- **Wallet system** - top-up via Paystack Popup, cashout to MoMo with configurable service fee
- **Personal savings goals** - with contribution scheduling and email reminders
- **AI financial assistant** - streaming Groq LLaMA chat scoped to savings and budgeting
- **Telegram bot integration** - contribution reminders, payout announcements, and member alerts
- **Invite link system** - shareable group invite links with auto-join and click tracking
- **Idempotent API** - all money-moving endpoints require `X-Idempotency-Key`
- **Scheduled tasks** - daily payouts, reconciliation, idempotency key cleanup via Vercel crons + QStash
- **Audit trail** - django-auditlog tracking on all financial models
- **Admin KYC** - Cloudinary-stored Ghana Card + live photo verification for group admins

<p align="right">(<a href="#readme-top">back to top</a>)</p>

---

## 🔗 Frontend <a name="frontend"></a>

> This is the backend repository only. The frontend is built with Next.js, Tailwind CSS, TanStack Query, and Recharts.

👉 [Snappx Frontend Repository](https://github.com/regenstudiosltd-arch/Snappexchange)

<p align="right">(<a href="#readme-top">back to top</a>)</p>

---

## 💻 Getting Started <a name="getting-started"></a>

### Prerequisites

- [Python](https://www.python.org/) 3.12+
- [PostgreSQL](https://www.postgresql.org/)
- [Redis](https://redis.io/) (optional locally — falls back to in-memory cache)

<p align="right">(<a href="#readme-top">back to top</a>)</p>

### Setup

Clone the repository:

```sh
git clone https://github.com/kessie2862/snappxx.git
cd snappxx
```

Create and activate a virtual environment:

```sh
python3.12 -m venv venv

# macOS/Linux
source venv/bin/activate

# Windows
.\venv\Scripts\Activate
```

Install dependencies:

```sh
pip install -r requirements.txt
```

<p align="right">(<a href="#readme-top">back to top</a>)</p>

### Environment Variables <a name="environment-variables"></a>

Create a `.env` file in the project root:

```env
# Core
SECRET_KEY=your-secret-key
DEBUG=True
DJANGO_ENV=development
ADMIN_PATH=admin

# Database (local)
POSTGRES_DB=snappx_db
POSTGRES_USER=postgres
POSTGRES_PASSWORD=yourpassword

# Database (production)
DATABASE_URL=postgresql://...

# Hashing & Encryption
HASH_SALT=your-random-salt
FIELD_ENCRYPTION_KEY=your-encryption-key

# Cloudinary (media storage)
CLOUDINARY_CLOUD_NAME=your-cloud-name
CLOUDINARY_API_KEY=your-api-key
CLOUDINARY_API_SECRET=your-api-secret

# Paystack (payments)
PAYSTACK_SECRET_KEY=sk_test_...
PAYSTACK_PUBLIC_KEY=pk_test_...

# Dawurobo (SMS OTP)
DAWUROBO_API_KEY=your-api-key
DAWUROBO_ACCESS_TOKEN=your-access-token
DAWUROBO_SENDER_ID=Snappx

# Groq (AI chat)
GROQ_API_KEY=gsk_...

# QStash (task queue)
QSTASH_TOKEN=your-token
QSTASH_CURRENT_SIGNING_KEY=your-signing-key

# Upstash Redis (cache + OTP lockout)
UPSTASH_REDIS_URL=rediss://...

# Telegram Bot
TELEGRAM_BOT_TOKEN=your-bot-token
TELEGRAM_WEBHOOK_SECRET=your-webhook-secret

# Email (Resend)
RESEND_API_KEY=re_...

# App URLs
FRONTEND_URL=https://www.snappx.app
SITE_URL=http://127.0.0.1:8000
```

<p align="right">(<a href="#readme-top">back to top</a>)</p>

### Run Locally <a name="run-locally"></a>

```sh
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

The API will be available at [http://localhost:8000](http://localhost:8000)

<p align="right">(<a href="#readme-top">back to top</a>)</p>

---

## 📡 API Endpoints <a name="api-endpoints"></a>

### Authentication (`/api/auth/`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/auth/signup/` | Register a new user with profile + MoMo |
| POST | `/api/auth/login/` | Login via email or MoMo phone number |
| POST | `/api/auth/token/refresh/` | Refresh JWT access token |
| GET  | `/api/auth/me/` | Get current authenticated user |
| POST | `/api/auth/otp/send/` | Send OTP to phone number |
| POST | `/api/auth/otp/verify/` | Verify OTP and activate account |
| POST | `/api/auth/forgot-password/` | Request password reset OTP |
| POST | `/api/auth/reset-password/` | Reset password with OTP |
| POST | `/api/auth/change-password/` | Change password (authenticated) |

### Profile & Dashboard (`/api/accounts/`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| PATCH | `/api/accounts/profile/` | Update profile (MoMo changes require OTP) |
| GET   | `/api/accounts/dashboard/` | Personalised savings dashboard |
| GET   | `/api/accounts/analytics/` | Full analytics with charts and insights |
| GET   | `/api/accounts/activity/` | Recent transaction history |
| GET   | `/api/accounts/scheduled-contributions/` | Upcoming contribution schedule |

### Savings Groups

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/accounts/groups/create/` | Create a new savings group (requires KYC) |
| GET  | `/api/accounts/groups/all/` | Browse all active groups |
| GET  | `/api/accounts/groups/my-joined/` | Groups the user is a member of |
| GET  | `/api/accounts/groups/stats/` | Aggregated group stats |
| GET  | `/api/accounts/groups/<public_id>/` | Full group detail with member roster |
| POST | `/api/accounts/groups/<group_id>/request_join/` | Request to join a group |
| POST | `/api/accounts/groups/<group_id>/contribute/` | Record a cycle contribution |
| GET  | `/api/accounts/groups/<group_id>/invite/` | Get group invite link |
| POST | `/api/accounts/groups/<group_id>/invite/` | Regenerate invite link (admin only) |
| GET  | `/api/accounts/invite/<token>/` | Preview an invite link |
| POST | `/api/accounts/invite/<token>/accept/` | Join via invite link |
| GET  | `/api/accounts/groups/<group_id>/requests/` | List join requests (admin) |
| POST | `/api/accounts/groups/requests/<pk>/action/` | Approve or reject a join request |
| GET  | `/api/accounts/groups/join-requests/stats/` | Join request counts for admin |

### Savings Goals

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST   | `/api/accounts/goals/create/` | Create a personal savings goal |
| GET    | `/api/accounts/goals/dashboard/` | Goals dashboard summary |
| GET/PATCH/DELETE | `/api/accounts/goals/<id>/` | Manage a single goal |
| POST   | `/api/accounts/goals/<goal_id>/contribute/` | Contribute to a goal |

### Payments

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/accounts/payments/topup/initialize/` | Initialize a Paystack top-up |
| POST | `/api/accounts/payments/cashout/` | Withdraw from wallet to MoMo |
| POST | `/api/accounts/payments/webhook/` | Paystack webhook receiver |
| GET  | `/api/accounts/payments/status/<reference>/` | Poll transaction status |

### Notifications

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET    | `/api/accounts/notifications/` | List notifications |
| GET    | `/api/accounts/notifications/unread-count/` | Unread notification count |
| POST   | `/api/accounts/notifications/mark-all-read/` | Mark all as read |
| POST   | `/api/accounts/notifications/<pk>/read/` | Mark single notification as read |
| DELETE | `/api/accounts/notifications/<pk>/delete/` | Delete a notification |
| DELETE | `/api/accounts/notifications/clear/` | Clear all notifications |

### AI Chat

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/accounts/ai/chat/` | Streaming AI savings assistant (SSE) |

### Telegram Bot Integration

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/accounts/groups/<group_id>/bot/generate-code/` | Generate a bot link code |
| POST | `/api/accounts/groups/bot/link/` | Link group to a bot platform |
| DELETE | `/api/accounts/groups/<group_id>/bot/unlink/` | Unlink a bot |
| GET  | `/api/accounts/groups/bot/linked/` | List linked groups |
| GET  | `/api/accounts/groups/bot/my-groups/` | Admin groups with bot status |
| POST | `/api/accounts/bot/telegram/webhook/` | Telegram update receiver |
| POST | `/api/accounts/bot/telegram/set-webhook/` | Register webhook (staff only) |
| GET  | `/api/accounts/bot/telegram/health/` | Bot health check (staff only) |

<p align="right">(<a href="#readme-top">back to top</a>)</p>

---

## 🧪 Running Tests <a name="running-tests"></a>

```sh
pytest
```

Tests use `--reuse-db` for speed. Settings module is configured via `pytest.ini`.

<p align="right">(<a href="#readme-top">back to top</a>)</p>

---

## 👥 Authors <a name="authors"></a>

👤 **Prosper Kessie**

- GitHub: [@kessie2862](https://github.com/kessie2862)
- LinkedIn: [Prosper Kessie](https://www.linkedin.com/in/prosperkessie/)

<p align="right">(<a href="#readme-top">back to top</a>)</p>

---
---

## 🔭 Future Features <a name="future-features"></a>

- [ ] WhatsApp bot integration
- [ ] In-app transaction dispute resolution
- [ ] Group admin dashboard with cycle analytics
- [ ] Multi-currency wallet support

<p align="right">(<a href="#readme-top">back to top</a>)</p>

---

## 🤝 Contributing <a name="contributing"></a>

Contributions, issues, and feature requests are welcome.

Feel free to check the [issues page](https://github.com/kessie2862/snappxx/issues).

<p align="right">(<a href="#readme-top">back to top</a>)</p>

---

## ⭐️ Show your support <a name="support"></a>

If you found this project useful, give it a ⭐️. It helps a lot.

<p align="right">(<a href="#readme-top">back to top</a>)</p>

---

## 📝 License <a name="license"></a>

This project is [MIT](./LICENSE) licensed.

<p align="right">(<a href="#readme-top">back to top</a>)</p>
