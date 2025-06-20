import json
import os
import boto3
import psycopg2
from datetime import datetime, timezone
from dotenv import load_dotenv
from botocore.exceptions import NoCredentialsError

# ----------------------
# Configuration des accès (à sécuriser avec un .env en production)
# ----------------------
load_dotenv()

AWS_ACCESS_KEY = os.getenv("AWS_ACCESS_KEY")
AWS_SECRET_KEY = os.getenv("AWS_SECRET_KEY")
BUCKET_NAME = os.getenv("BUCKET_NAME")
REGION_NAME = os.getenv("REGION_NAME")

DB_HOST = os.getenv("DB_HOST")
DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASS = os.getenv("DB_PASS")

# ----------------------
# Connexions
# ----------------------
s3 = boto3.client('s3',
                  aws_access_key_id=AWS_ACCESS_KEY,
                  aws_secret_access_key=AWS_SECRET_KEY,
                  region_name=REGION_NAME)

def get_db_connection():
    return psycopg2.connect(
        host=DB_HOST,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASS
    )

# ----------------------
# Exécution de requêtes générique
# ----------------------
def execute_query(query, params=None, fetch=False, returning=False):
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(query, params)
            if returning:
                result = cursor.fetchone()[0]
                conn.commit()
                return result
            if fetch:
                return cursor.fetchone()
            conn.commit()

# ----------------------
# Fonctions utilitaires avec contrôles robustes
# ----------------------
def get_or_create_city(name, latitude, longitude, country="FR"):
    result = execute_query("SELECT id FROM common.city WHERE name=%s", (name,), fetch=True)
    if result:
        return result[0]
    return execute_query(
        """
        INSERT INTO common.city (name, latitude, longitude, country) 
        VALUES (%s, %s, %s, %s) RETURNING id
        """,
        (name, latitude, longitude, country), returning=True
    )

def get_or_create_datetime(timestamp_utc):
    dt = datetime.fromisoformat(timestamp_utc.replace('Z', '+00:00'))
    result = execute_query("SELECT id FROM common.date_time WHERE timestamp=%s", (dt,), fetch=True)
    if result:
        return result[0]
    return execute_query("""
        INSERT INTO common.date_time (timestamp, year, month, day, hour, timezone) 
        VALUES (%s, %s, %s, %s, %s, %s) RETURNING id
    """, (dt, dt.year, dt.month, dt.day, dt.hour, '+00:00'), returning=True)

def get_or_create_pollutant(code, description=""):
    result = execute_query("SELECT id FROM pollution.pollutant WHERE pollutant_code=%s", (code,), fetch=True)
    if result:
        return result[0]
    return execute_query("""
        INSERT INTO pollution.pollutant (pollutant_code, description) VALUES (%s, %s) RETURNING id
    """, (code, description), returning=True)

def get_or_create_weather_condition(main, description, icon):
    result = execute_query("""
        SELECT id FROM weather.weather_condition 
        WHERE main_condition=%s AND description=%s
    """, (main, description), fetch=True)
    if result:
        return result[0]
    return execute_query("""
        INSERT INTO weather.weather_condition (main_condition, description, icon) 
        VALUES (%s, %s, %s) RETURNING id
    """, (main, description, icon), returning=True)

def insert_wind(speed, degree, gust):
    return execute_query("""
        INSERT INTO weather.wind (speed, degree, gust) VALUES (%s, %s, %s) RETURNING id
    """, (speed, degree, gust), returning=True)

def insert_cloud(coverage):
    return execute_query("""
        INSERT INTO weather.cloud (coverage_percent) VALUES (%s) RETURNING id
    """, (coverage,), returning=True)

def insert_sun(sunrise_utc, sunset_utc):
    sunrise = datetime.fromtimestamp(sunrise_utc, tz=timezone.utc)
    sunset = datetime.fromtimestamp(sunset_utc, tz=timezone.utc)
    return execute_query("""
        INSERT INTO weather.sun (sunrise, sunset) VALUES (%s, %s) RETURNING id
    """, (sunrise, sunset), returning=True)

# ----------------------
# Traitement des données Air Quality
# ----------------------
def process_air_quality(data):
    if data.get('status') != 'ok':
        return

    city_info = data['data']['city']
    city_name = city_info['name']
    latitude, longitude = city_info['geo']
    city_id = get_or_create_city(city_name, latitude, longitude)
    if city_id is None:
        return

    time_info = data['data'].get('time', {})
    timestamp = time_info.get('iso')

    if not timestamp:
        print(f"⛔ Données incomplètes pour la ville : {city_name}. Clé 'iso' manquante dans 'time'.")
        return  # Ne pas continuer sans timestamp

    datetime_id = get_or_create_datetime(timestamp)

    iaqi = data['data'].get('iaqi', {})
    for pol, details in iaqi.items():
        value = details.get('v')
        if value is not None:
            pollutant_id = get_or_create_pollutant(pol)
            execute_query("""
                INSERT INTO pollution.air_quality_fact (city_id, datetime_id, pollutant_id, value) 
                VALUES (%s, %s, %s, %s)
            """, (city_id, datetime_id, pollutant_id, value))

# ----------------------
# Traitement des données Météo
# ----------------------
def process_weather(data):
    city_name = data['name']
    latitude = data['coord']['lat']
    longitude = data['coord']['lon']
    city_id = get_or_create_city(city_name, latitude, longitude)
    if city_id is None:
        return

    timestamp = datetime.utcfromtimestamp(data['dt']).isoformat()
    datetime_id = get_or_create_datetime(timestamp)

    main_data = data['main']
    weather = data['weather'][0]
    wind = data['wind']
    sys_data = data['sys']
    clouds = data['clouds']

    weather_condition_id = get_or_create_weather_condition(weather['main'], weather['description'], weather['icon'])
    wind_id = insert_wind(wind['speed'], wind['deg'], wind.get('gust'))
    cloud_id = insert_cloud(clouds['all'])
    sun_id = insert_sun(sys_data['sunrise'], sys_data['sunset'])

    execute_query("""
        INSERT INTO weather.weather_fact (
            city_id, datetime_id, temperature, feels_like, temp_min, temp_max, 
            humidity, pressure, visibility, weather_condition_id, wind_id, cloud_id, sun_id
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (
        city_id, datetime_id, main_data['temp'], main_data['feels_like'],
        main_data['temp_min'], main_data['temp_max'], main_data['humidity'],
        main_data['pressure'], data.get('visibility', 10000),
        weather_condition_id, wind_id, cloud_id, sun_id
    ))

# ----------------------
# Pipeline Principal ETL
# ----------------------
def main():
    try:
        print("🚀 Début de l'ETL GoodAir...")

        # Traitement des fichiers Air Quality
        air_files = s3.list_objects_v2(Bucket=BUCKET_NAME, Prefix='air_quality/').get('Contents', [])
        for obj in air_files:
            key = obj['Key']
            content = s3.get_object(Bucket=BUCKET_NAME, Key=key)['Body'].read().decode('utf-8')
            data = json.loads(content)
            process_air_quality(data)

        # Traitement des fichiers Weather
        weather_files = s3.list_objects_v2(Bucket=BUCKET_NAME, Prefix='weather/').get('Contents', [])
        for obj in weather_files:
            key = obj['Key']
            content = s3.get_object(Bucket=BUCKET_NAME, Key=key)['Body'].read().decode('utf-8')
            data = json.loads(content)
            process_weather(data)

        print("✅ ETL terminé avec succès.")

    except NoCredentialsError:
        print("❌ Erreur : Identifiants AWS non valides ou manquants.")
    except Exception as e:
        print(f"❌ Erreur durant l'ETL : {e}")

if __name__ == "__main__":
    main()
