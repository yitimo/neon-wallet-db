# Steps on how to run this project

## Environment
I recommend to run on a linux-based virtual machine (Debian 9 is good enough)

Then you need python3 installed. I recommend to use pyenv & virtualenv to manage your python version.

Here are some useful links:

1. [python.org](https://www.python.org/downloads/)
2. [pyenv](https://github.com/pyenv/pyenv)
3. [virtualenv](https://github.com/pypa/virtualenv)

## Heroku local
The project is configured to run by [heroku](https://signup.heroku.com). For our develop purpose we just need to install its [CLI](https://devcenter.heroku.com/articles/heroku-cli).
When everything have been setted. Just use ``heroku local`` to run our project.

## MongoDB & Redis
The project uses MongoDB and Redis to save transaction data and other useful things. Just install them and run under default config(for dev purpose).
Then configure ``./api/db.py`` to match your db config.

## You are done
If all of above is fine, now just run ``heroku local``, and the project will run 3 things. One for web api, one for sync block data, one for worker.
