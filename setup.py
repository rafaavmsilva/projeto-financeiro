from setuptools import setup, find_packages

setup(
    name="auth_client",
    version="0.1",
    py_modules=["auth_client"],
    install_requires=[
        "requests>=2.26.0",
        "Flask>=2.0.1"
    ]
)
