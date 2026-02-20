# Daiby - AI Database Assistant

Daiby is an AI-powered natural language database assistant that converts your questions into SQL queries. It supports multiple LLM providers (Gemini, OpenAI, Azure, Anthropic) and multiple database connections.

## Features

- **Natural Language to SQL**: Ask questions in plain English, get SQL queries
- **Multi-LLM Support**: Choose from Gemini, OpenAI GPT, Azure OpenAI, Anthropic Claude, or local Ollama
- **Multiple Databases**: Configure and switch between multiple database connections
- **Interactive REPL**: Rich command-line interface with autocomplete and history
- **Safe by Default**: SQL is generated but not executed unless you ask for results
- **Clipboard Integration**: SQL is automatically copied to clipboard
- **Export Options**: Save results as CSV or display as markdown tables

## Installation

```bash
# Basic installation (no LLM providers)
pip install daiby

# With specific LLM provider
pip install daiby[gemini]
pip install daiby[openai]
pip install daiby[anthropic]

# With all providers
pip install daiby[all]
```

## Quick Start

### 1. Create Configuration

Create a `daiby.yaml` file (or copy from `daiby.yaml.example`):

```yaml
llm:
  default: gemini
  providers:
    gemini:
      type: gemini
      model: gemini-2.5-pro
      api_key: ${GEMINI_API_KEY}

databases:
  default: mydb
  mydb:
    host: localhost
    port: 3306
    name: my_database
    user: ${DB_USER}
    password: ${DB_PASSWORD}
```

### 2. Set Environment Variables

Create a `.env` file:

```bash
GEMINI_API_KEY=your-api-key
DB_USER=your_user
DB_PASSWORD=your_password
```

### 3. Run Daiby

```bash
# Interactive mode
daiby

# Single query
daiby "show me all users"
```

## Usage Examples

### Generating SQL (Default - No Execution)

```
> join users and orders
Generated SQL:
SELECT u.*, o.*
FROM users u
JOIN orders o ON u.id = o.user_id;
```

### Getting Results

```
> show me the top 10 customers
Generated SQL:
SELECT * FROM customers LIMIT 10;

Results (10 rows):
...
```

### Exporting to CSV

```
> export csv all orders from last month
Saved 142 rows to: orders_last_month.csv
```

### DDL Mode

```
> @ddl create a view for active users
Generated SQL:
CREATE OR REPLACE VIEW active_users AS
SELECT * FROM users WHERE status = 'active';
```

### Switching Databases

```
> @use production
Switched to database: production

> @databases
Available databases:
  production (current)
  staging
  development
```

### Switching LLM Providers

```
> @llm openai
Switched to LLM: openai

> @providers
Available LLM providers:
  gemini
  openai (current)
  anthropic
```

## Commands

| Command | Description |
|---------|-------------|
| `@use <db>` | Switch to named database |
| `@llm <name>` | Switch LLM provider |
| `@databases` | List available databases |
| `@providers` | List available LLM providers |
| `@sql` | SQL mode (SELECT queries) |
| `@ddl` | DDL mode (CREATE/ALTER/DROP) |
| `@crud` | CRUD mode (INSERT/UPDATE/DELETE) |
| `@schema` | Show database schema |
| `@tables` | List tables |
| `@clipboard` | Toggle clipboard copy |
| `@verbose` | Toggle verbose mode |
| `@help` | Show help |
| `@examples` | Show usage examples |
| `@quit` | Exit |

## Configuration

Daiby looks for configuration in these locations (in order):

1. `./daiby.yaml`
2. `./.daiby.yaml`
3. `~/.daiby/daiby.yaml`
4. `~/.config/daiby/daiby.yaml`

Environment variables are loaded from:

1. `./.env`
2. `~/.daiby/.env`

### Full Configuration Example

See `daiby.yaml.example` for a complete configuration example.

## LLM Providers

### Google Gemini

```yaml
gemini:
  type: gemini
  model: gemini-2.5-pro
  api_key: ${GEMINI_API_KEY}
  temperature: 0.7
```

### OpenAI GPT

```yaml
openai:
  type: openai
  model: gpt-4o
  api_key: ${OPENAI_API_KEY}
  organization: ${OPENAI_ORG}  # Optional
```

### Azure OpenAI

```yaml
azure:
  type: azure
  deployment: my-gpt4-deployment
  endpoint: https://myresource.openai.azure.com
  api_key: ${AZURE_OPENAI_KEY}
  api_version: "2024-02-01"
```

### Anthropic Claude

```yaml
anthropic:
  type: anthropic
  model: claude-3-5-sonnet-20241022
  api_key: ${ANTHROPIC_API_KEY}
```

### Local Ollama

```yaml
ollama:
  type: ollama
  model: codellama:13b
  host: http://localhost:11434
```

## Development

```bash
# Clone the repository
git clone https://github.com/amramedworkin/daiby.git
cd daiby

# Create virtual environment
python -m venv .venv
source .venv/bin/activate

# Install with dev dependencies
pip install -e ".[dev,all]"

# Run tests
pytest
```

## License

MIT License - see LICENSE file for details.
