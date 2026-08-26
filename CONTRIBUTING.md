# Contributing to Cello

Thank you for your interest in contributing to Cello! 🐍

## Getting Started

### Prerequisites

- Python 3.12+
- Rust 1.70+
- maturin (`python3.12 -m pip install maturin`)

### Development Setup

```bash
# Clone the repository
git clone https://github.com/jagadeesh32/cello.git
cd cello

# Create the environment with Python 3.12 explicitly.
# On Windows use: py -3.12 -m venv .venv
python3.12 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Verify the active interpreter before installing anything
python --version  # Must be Python 3.12+

# Install development dependencies
python -m pip install maturin pytest ruff requests

# Build the project with the active Python 3.12 environment
python -m maturin develop

# Run tests
pytest tests/ -v
```

## Making Changes

### 1. Create a Branch

```bash
git checkout -b feature/your-feature-name
```

### 2. Make Your Changes

- **Rust code** → `src/` directory
- **Python wrapper** → `python/cello/` directory
- **Tests** → `tests/` directory

### 3. Test Your Changes

```bash
# Rebuild after Rust changes
python -m maturin develop

# Run Python tests
pytest tests/ -v

# Run linters
ruff check python/ tests/
cargo clippy
cargo fmt --check
```

### 4. Commit Your Changes

```bash
git add .
git commit -m "feat: add your feature description"
```

Follow [Conventional Commits](https://www.conventionalcommits.org/):
- `feat:` New feature
- `fix:` Bug fix
- `docs:` Documentation
- `refactor:` Code refactoring
- `test:` Adding tests
- `chore:` Maintenance

### 5. Push and Create PR

```bash
git push origin feature/your-feature-name
```

Then create a Pull Request on GitHub.

## Code Style

### Rust

- Follow Rust standard style (`cargo fmt`)
- No clippy warnings (`cargo clippy -- -D warnings`)
- Document public APIs with `///` comments

### Python

- Follow PEP 8
- Use ruff for linting
- Type hints encouraged

## Project Structure

```
cello/
├── src/                       # Rust source code
│   ├── lib.rs                 # Main entry, Python module
│   ├── request.rs             # Request handling
│   ├── response.rs            # Response types
│   ├── router.rs              # URL routing
│   ├── handler.rs             # Handler registry
│   ├── blueprint.rs           # Route grouping
│   ├── websocket.rs           # WebSocket support
│   ├── sse.rs                 # Server-Sent Events
│   ├── multipart.rs           # File uploads
│   ├── json.rs                # SIMD JSON
│   ├── arena.rs               # Arena allocators
│   ├── context.rs             # Request context, DI container
│   ├── error.rs               # RFC 7807 error handling
│   ├── lifecycle.rs           # Hooks and lifecycle events
│   ├── timeout.rs             # Timeout and limits
│   ├── middleware/            # Middleware modules
│   │   ├── mod.rs             # Core middleware traits
│   │   ├── auth.rs            # JWT, Basic, API Key auth
│   │   ├── rate_limit.rs      # Rate limiting
│   │   ├── session.rs         # Cookie sessions
│   │   ├── security.rs        # Security headers, CSP
│   │   ├── csrf.rs            # CSRF protection
│   │   ├── static_files.rs    # Static file serving
│   │   ├── body_limit.rs      # Body size limits
│   │   ├── request_id.rs      # Request ID generation
│   │   ├── etag.rs            # ETag caching
│   │   └── cors.rs            # CORS handling
│   ├── routing/               # Routing modules
│   │   └── mod.rs             # Route constraints, versioning
│   ├── request/               # Request modules
│   │   └── mod.rs             # Lazy parsing, typed params
│   ├── response/              # Response modules
│   │   └── mod.rs             # Streaming, XML
│   └── server/                # Server modules
│       └── mod.rs             # Cluster, TLS, HTTP/2, HTTP/3
├── python/cello/              # Python package
│   └── __init__.py            # Python API wrapper
├── tests/                     # Python tests
├── examples/                  # Example applications
│   ├── hello.py               # Basic example
│   ├── advanced.py            # Advanced features
│   ├── enterprise.py          # Enterprise configurations
│   ├── security.py            # Security features
│   ├── middleware_demo.py     # Middleware demo
│   ├── cluster_demo.py        # Cluster mode demo
│   └── streaming_demo.py      # SSE and streaming
├── docs/                      # Documentation
│   ├── README.md              # Documentation index
│   ├── getting-started.md     # Installation and basics
│   ├── configuration.md       # Configuration reference
│   ├── middleware.md          # Middleware guide
│   ├── security.md            # Security guide
│   ├── enterprise.md          # Enterprise features
│   ├── routing.md             # Routing guide
│   ├── api-reference.md       # API reference
│   ├── deployment.md          # Deployment guide
│   └── changelog.md           # Version history
├── Cargo.toml                 # Rust dependencies
└── pyproject.toml             # Python project config
```

## Questions?

Open an issue on GitHub!

