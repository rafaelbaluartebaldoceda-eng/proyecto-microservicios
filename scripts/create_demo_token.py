from app.core.security import AuthenticatedUser, create_access_token


def main() -> None:
    user = AuthenticatedUser(user_id="demo-user", email="demo@example.com", roles=["admin"])
    print(create_access_token(user))


if __name__ == "__main__":
    main()
