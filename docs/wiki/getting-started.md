# Getting started: run desktop-inbox from a clone

In this tutorial, we clone the plugin, install it in a throwaway Hermes home, check it with the Hermes validator, run its test suite and print its item types. The throwaway home keeps your real Hermes sessions and settings out of the tutorial.

We stop before we file an item, because filing an item needs a configured model. The [project README](../../README.md#send-your-first-item) shows how to file your first item in your real Hermes home.

## Before we start

You need:

- Hermes 0.23.1 or later, installed with the standard installer, so that its Python is at `~/.hermes/hermes-agent/venv/bin/python`.
- `git`.
- A terminal. Run every command in the same terminal window, because step 2 sets an environment variable.

## Step 1: clone the plugin

Make a work folder and go into it:

```bash
mkdir inbox-tutorial && cd inbox-tutorial
```

Clone the repository:

```bash
git clone https://github.com/Nj07r/hermes-desktop-inbox
```

The output is:

```text
Cloning into 'hermes-desktop-inbox'...
```

## Step 2: make a throwaway Hermes home

Point Hermes at a new folder in the work folder:

```bash
export HERMES_HOME="$PWD/home"
```

Make the plugins folder in it:

```bash
mkdir -p "$HERMES_HOME/plugins"
```

These two commands print nothing.

## Step 3: install and enable the plugin

Link the clone into the plugins folder:

```bash
ln -s "$PWD/hermes-desktop-inbox" "$HERMES_HOME/plugins/desktop-inbox"
```

Enable the plugin:

```bash
hermes plugins enable desktop-inbox
```

The output is:

```text
✓ Plugin desktop-inbox enabled.
Restart the gateway for the plugin to take effect:
  hermes gateway restart
```

Do not restart the gateway. The CLI part of the plugin works without the gateway.

## Step 4: validate the plugin

Run the Hermes plugin validator on the clone:

```bash
hermes plugins validate ./hermes-desktop-inbox
```

The output starts with a list of checks and ends like this:

```text
✓ no core override — no runtime rebinds of Hermes core
✓ desktop surface — stays inside the plugin SDK surface
⚠ security scan caution: env_exfil_curl (README.md:208)
◆ Isolation — runs in the plugin host (plugins.isolation: host)
  · dashboard backend API is served through the plugin host (buffered responses)
  · ctx.register_cli_command() is skipped in the host (`hermes <command>` 
subcommands are wired into the local CLI parser)

Validation passed.
```

Notice the last line, `Validation passed.` The caution points at the `curl` example in the project README, which sends the webhook token in a header. It is not an error.

## Step 5: run the tests

Run the test suite with the Python that runs Hermes:

```bash
~/.hermes/hermes-agent/venv/bin/python -m unittest discover -s hermes-desktop-inbox/tests
```

After about one second, the output is:

```text
..............................
----------------------------------------------------------------------
Ran 30 tests in 0.531s

OK
```

The time changes a little on each run.

## Step 6: print the item types

Ask the plugin for its item types:

```bash
hermes desktop-inbox types
```

The output is:

```text
📖 read       Read: {user} reads it, then says 'read' (or 'done') to clear it.
💬 answer     Answer: {user} answers the question(s) in this conversation.
⚖️ decide     Decide: {user} picks between the options in this conversation.
✅ do         Do: {user} does the task (outside or with Hermes), then says 'done'.
```

These are the four built-in types from `BUILTIN_TYPES` in `desktop_inbox/settings.py`. The `{user}` placeholder becomes the `user_name` setting when an item is filed.

## Step 7: clean up

Go back to the parent folder:

```bash
cd ..
```

Remove the work folder:

```bash
rm -rf inbox-tutorial
```

Remove the variable, so that Hermes uses your real home again:

```bash
unset HERMES_HOME
```

## What we did

We installed the plugin from source in a throwaway Hermes home, and we saw it pass the validator and all 30 tests. The CLI loaded from the clone and read its types from the code. This is the same setup that the README "Develop" section uses, without your real Hermes data.

## Where to go next

- [architecture.md](architecture.md): why an item is a real session and how state works.
- [api.md](api.md): every CLI command and HTTP route.
- [modules/core.md](modules/core.md): the functions behind each command.
