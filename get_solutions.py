#!/usr/bin/env python3
"""
Скрипт для выгрузки решений студентов с GitLab.
Автоматически находит все репозитории студентов.
Структура выходных данных: task_name/<student_nickname>.h
"""

import os
import re
import subprocess
import tempfile
import shutil
import yaml
from pathlib import Path
from typing import Optional
import argparse
import requests
from urllib.parse import quote_plus


# Задачи, которые нужно исключить (не C++)
EXCLUDED_PATTERNS = [
    r'^3-ilp-',
    r'^6-ast',
    r'^7-query',
    r'^8-pass-',
]

GITLAB_URL = "https://gitlab.manytask.org"
GROUP_PATH = "pe101/students-2025-fall"


def is_excluded_task(task_name: str) -> bool:
    """Проверяет, нужно ли исключить задачу."""
    for pattern in EXCLUDED_PATTERNS:
        if re.match(pattern, task_name):
            return True
    return False


def is_valid_task_folder(folder_name: str) -> bool:
    """Проверяет, соответствует ли папка формату <week_number>-<name>-<task_number>."""
    pattern = r'^\d+-[a-zA-Z]+'
    return bool(re.match(pattern, folder_name))


def parse_task_yaml(task_yaml_path: Path) -> Optional[list[str]]:
    """Парсит .task.yml и возвращает список файлов из allow_change."""
    try:
        with open(task_yaml_path, 'r') as f:
            data = yaml.safe_load(f)
            if data and 'parameters' in data and 'allow_change' in data['parameters']:
                allow_change = data['parameters']['allow_change']
                if isinstance(allow_change, list):
                    return allow_change
                return [allow_change]
    except Exception as e:
        print(f"  Ошибка парсинга {task_yaml_path}: {e}")
    return None


def normalize_content(content: str) -> str:
    """Нормализует контент для сравнения (убирает namespace различия)."""
    # Заменяем namespace Solution на Copy для унификации
    normalized = content.replace('namespace Solution', 'namespace Copy')
    normalized = normalized.replace('Solution::', 'Copy::')
    return normalized


def has_student_changes(solution_path: Path, copy_path: Path) -> bool:
    """
    Проверяет, внёс ли студент изменения в файл.
    Сравнивает с .copy.h, учитывая что namespace отличается.
    """
    if not solution_path.exists():
        return False
    
    if not copy_path.exists():
        # Если нет .copy.h, считаем что изменения есть
        return True
    
    try:
        with open(solution_path, 'r', encoding='utf-8', errors='ignore') as f:
            solution_content = f.read()
        with open(copy_path, 'r', encoding='utf-8', errors='ignore') as f:
            copy_content = f.read()
        
        # Нормализуем и сравниваем
        norm_solution = normalize_content(solution_content)
        norm_copy = normalize_content(copy_content)
        
        return norm_solution.strip() != norm_copy.strip()
    except Exception as e:
        print(f"  Ошибка сравнения файлов: {e}")
        return False


def get_all_students(token: str) -> list[dict]:
    """Получает список всех студентов (проектов) через GitLab API."""
    headers = {'PRIVATE-TOKEN': token}
    
    print("🔍 Получаю список студентов из GitLab...")
    
    # Получаем ID группы
    encoded_path = quote_plus(GROUP_PATH)
    response = requests.get(
        f"{GITLAB_URL}/api/v4/groups/{encoded_path}",
        headers=headers
    )
    
    if response.status_code != 200:
        print(f"❌ Ошибка получения группы: {response.status_code}")
        print(f"   {response.text}")
        return []
    
    group_id = response.json()['id']
    print(f"   Группа найдена, ID: {group_id}")
    
    # Получаем все проекты в группе (с пагинацией)
    students = []
    page = 1
    per_page = 100
    
    while True:
        response = requests.get(
            f"{GITLAB_URL}/api/v4/groups/{group_id}/projects",
            headers=headers,
            params={
                'page': page,
                'per_page': per_page,
                'include_subgroups': False,
                'simple': True
            }
        )
        
        if response.status_code != 200:
            print(f"❌ Ошибка получения проектов: {response.status_code}")
            break
        
        projects = response.json()
        if not projects:
            break
        
        for project in projects:
            students.append({
                'name': project['path'],
                'http_url': project['http_url_to_repo'],
                'id': project['id']
            })
        
        print(f"   Загружено {len(students)} студентов...")
        page += 1
    
    print(f"✅ Всего найдено студентов: {len(students)}")
    return students


def clone_repository(repo_url: str, target_dir: Path, token: str) -> bool:
    """Клонирует репозиторий студента с использованием токена."""
    # Вставляем токен в URL
    auth_url = repo_url.replace('https://', f'https://oauth2:{token}@')
    
    try:
        result = subprocess.run(
            ['git', 'clone', '--depth', '1', '--quiet', auth_url, str(target_dir)],
            capture_output=True,
            text=True,
            timeout=120
        )
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        print(f"  ⏱️ Таймаут")
        return False
    except Exception as e:
        print(f"  ❌ Ошибка: {e}")
        return False


def find_copy_file(task_dir: Path, solution_file: str) -> Optional[Path]:
    """Ищет соответствующий .copy.h файл."""
    # Вариант 1: solution.copy.h
    copy_filename = solution_file.replace('.h', '.copy.h')
    copy_path = task_dir / copy_filename
    if copy_path.exists():
        return copy_path
    
    # Вариант 2: .copy.h с таким же базовым именем
    base_name = solution_file.replace('.h', '')
    for f in task_dir.glob('*.copy.h'):
        if base_name in f.name:
            return f
    
    # Вариант 3: любой .copy.h в папке
    copy_files = list(task_dir.glob('*.copy.h'))
    if len(copy_files) == 1:
        return copy_files[0]
    
    return None


def process_student(student: dict, output_dir: Path, temp_base: Path, token: str) -> dict[str, int]:
    """Обрабатывает репозиторий одного студента."""
    stats = {'found': 0, 'skipped_no_changes': 0, 'skipped_excluded': 0, 'errors': 0}
    
    student_name = student['name']
    student_temp_dir = temp_base / student_name
    
    if not clone_repository(student['http_url'], student_temp_dir, token):
        stats['errors'] = 1
        return stats
    
    # Проходим по всем папкам в репозитории
    try:
        items = list(student_temp_dir.iterdir())
    except Exception:
        stats['errors'] = 1
        return stats
    
    for item in items:
        if not item.is_dir():
            continue
        
        task_name = item.name
        
        # Пропускаем служебные папки
        if task_name.startswith('.'):
            continue
        
        # Проверяем формат имени
        if not is_valid_task_folder(task_name):
            continue
        
        # Проверяем исключения
        if is_excluded_task(task_name):
            stats['skipped_excluded'] += 1
            continue
        
        task_dir = item
        task_yaml = task_dir / '.task.yml'
        
        if not task_yaml.exists():
            continue
        
        # Получаем список файлов с решением
        solution_files = parse_task_yaml(task_yaml)
        if not solution_files:
            continue
        
        for solution_file in solution_files:
            if not solution_file.endswith('.h'):
                continue
            
            solution_path = task_dir / solution_file
            if not solution_path.exists():
                continue
            
            # Ищем соответствующий .copy.h
            copy_path = find_copy_file(task_dir, solution_file)
            
            # Проверяем наличие изменений
            if copy_path and not has_student_changes(solution_path, copy_path):
                stats['skipped_no_changes'] += 1
                continue
            
            # Копируем решение
            task_output_dir = output_dir / task_name
            task_output_dir.mkdir(parents=True, exist_ok=True)
            
            output_filename = f"{student_name}.h"
            output_path = task_output_dir / output_filename
            
            shutil.copy2(solution_path, output_path)
            stats['found'] += 1
    
    # Удаляем временную папку студента
    shutil.rmtree(student_temp_dir, ignore_errors=True)
    
    return stats


def main():
    parser = argparse.ArgumentParser(description='Выгрузка решений студентов с GitLab')
    parser.add_argument(
        '-o', '--output',
        default='solutions',
        help='Выходная директория (default: solutions)'
    )
    parser.add_argument(
        '-t', '--token',
        help='GitLab токен (или переменная окружения GITLAB_TOKEN)'
    )
    parser.add_argument(
        '--limit',
        type=int,
        default=0,
        help='Ограничить количество студентов (для тестирования)'
    )
    
    args = parser.parse_args()
    
    # Получаем токен
    token = args.token or os.environ.get('GITLAB_TOKEN')
    if not token:
        print("❌ Нужен GitLab токен!")
        print()
        print("Как получить:")
        print("1. Зайди на https://gitlab.manytask.org")
        print("2. Settings → Access Tokens → Add new token")
        print("3. Scopes: read_api, read_repository")
        print("4. Создай и скопируй токен")
        print()
        print("Использование:")
        print("  export GITLAB_TOKEN='твой-токен'")
        print("  python download_solutions.py")
        print()
        print("Или:")
        print("  python download_solutions.py -t 'твой-токен'")
        return
    
    # Получаем список студентов
    students = get_all_students(token)
    if not students:
        print("❌ Не удалось получить список студентов")
        return
    
    if args.limit > 0:
        students = students[:args.limit]
        print(f"⚠️  Ограничено до {args.limit} студентов (для тестирования)")
    
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    print(f"\n📁 Выходная директория: {output_dir}")
    print(f"🎓 Обрабатываю {len(students)} студентов...\n")
    
    total_stats = {'found': 0, 'skipped_no_changes': 0, 'skipped_excluded': 0, 'errors': 0}
    
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_base = Path(temp_dir)
        
        for i, student in enumerate(students, 1):
            # Прогресс-бар
            progress = f"[{i:3d}/{len(students)}]"
            print(f"{progress} {student['name']:<30}", end=' ', flush=True)
            
            stats = process_student(student, output_dir, temp_base, token)
            
            # Результат для этого студента
            if stats['errors']:
                print("❌ ошибка клонирования")
            elif stats['found'] == 0:
                print("⏭️  нет решений")
            else:
                print(f"✅ {stats['found']} решений")
            
            for key in total_stats:
                total_stats[key] += stats[key]
    
    # Итоговая статистика
    print("\n" + "=" * 60)
    print("📊 ИТОГО:")
    print(f"   ✅ Скачано решений:              {total_stats['found']}")
    print(f"   ⏭️  Пропущено (без изменений):    {total_stats['skipped_no_changes']}")
    print(f"   🚫 Пропущено (не C++ задачи):    {total_stats['skipped_excluded']}")
    print(f"   ❌ Ошибки клонирования:          {total_stats['errors']}")
    print("=" * 60)
    
    # Показываем структуру
    print(f"\n📂 Результат в папке: {output_dir}/")
    task_dirs = sorted([d for d in output_dir.iterdir() if d.is_dir()])
    for task_dir in task_dirs[:10]:
        count = len(list(task_dir.glob('*.h')))
        print(f"   {task_dir.name}/ ({count} решений)")
    if len(task_dirs) > 10:
        print(f"   ... и ещё {len(task_dirs) - 10} задач")


if __name__ == '__main__':
    main()
