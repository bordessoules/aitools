$env:GOOSE_PROVIDER = "openai"
$env:GOOSE_MODEL = "openai/gpt-oss-20b"
$env:OPENAI_API_KEY = "not-needed"
$env:OPENAI_HOST = "http://bluefin:1234"

& "C:\Users\linkr\goose\goose.exe" run -t "Use your mcp_gateway_search tool to search the web for 'best AI agent frameworks 2025'. Summarize the top 3 results in a short list." --no-session --quiet --max-turns 3
