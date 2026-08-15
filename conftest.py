# Root-level conftest so pytest's collection walk never tries to stat .env.
# (Only matters in sandboxed dev environments that deny reading dotfiles by
# policy -- a real checkout/CI runner has no such restriction, and .env is
# gitignored so CI never has one at all -- but this keeps local runs robust.)
collect_ignore = [".env"]
collect_ignore_glob = [".env*"]
