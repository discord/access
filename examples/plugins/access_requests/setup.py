from setuptools import setup

setup(
    name='access-requests',
    install_requires=['pluggy==1.4.0'],
    py_modules=['access_requests'],
    entry_points={
        'access_requests': ['discord_requests = discord_requests'],
    },
)
