import hashlib
import secrets
import mysql.connector
import sys

def create_user():
    try:
        conn = mysql.connector.connect(
            user="root",
            unix_socket="/var/run/mysqld/mysqld.sock",
            database="openpagingserver"
        )
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM users")
        user_count = cursor.fetchone()[0]

        if user_count == 0:
            print("This is the first user on the system. The user will be an administrator.")
            role = "admin"
            user_id = 0
        else:
            user_id = None
            role_map = {"1": "admin", "2": "user", "3": "receiver"}
            while True:
                print("Select role:")
                print("1 = admin")
                print("2 = user")
                print("3 = receiver")
                role_choice = input("Enter number: ").strip()
                if role_choice in role_map:
                    role = role_map[role_choice]
                    break
                print("Invalid selection. Please enter 1, 2, or 3.")

        while True:
            username = input("Enter username: ").strip()
            if username:
                break
            print("Username cannot be empty.")

        email = input("Enter email (enter for none): ").strip() or None

        while True:
            password = input("Enter password: ").strip()
            if not password:
                print("Password cannot be empty.")
                continue
            confirm_password = input("Confirm password: ").strip()
            if password == confirm_password:
                break
            print("Passwords do not match. Please try again.")

        salt = secrets.token_hex(16)
        verifier_input = password + salt
        verifier = hashlib.sha256(verifier_input.encode()).hexdigest()

        if user_id is not None:
            sql = """INSERT INTO users (id, username, email, password, salt, role) 
                     VALUES (%s, %s, %s, %s, %s, %s)"""
            cursor.execute(sql, (user_id, username, email, verifier, salt, role))
        else:
            sql = """INSERT INTO users (username, email, password, salt, role) 
                     VALUES (%s, %s, %s, %s, %s)"""
            cursor.execute(sql, (username, email, verifier, salt, role))

        conn.commit()
        print(f"\nUser '{username}' created successfully!")

        conn.close()

    except mysql.connector.Error as e:
        print("Error:", e)

if __name__ == "__main__":
    create_user()
