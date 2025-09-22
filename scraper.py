import requests
from bs4 import BeautifulSoup
import json

def get_subjects(session, url):
    """
    Gets a list of subjects and their URLs from the classroom page.
    """
    try:
        response = session.get(url)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"Error fetching subjects: {e}")
        return None

    soup = BeautifulSoup(response.text, 'html.parser')
    subjects = {}

    subject_elements = soup.find_all('li', class_='media')

    for subject_element in subject_elements:
        media_body = subject_element.find('div', class_='media-body')
        if media_body:
            h5 = media_body.find('h5')
            if h5:
                subject_link = h5.find('a')
                if subject_link:
                    subject_name = subject_link.text.strip()
                    subject_url = subject_link['href']
                    subjects[subject_name] = subject_url

    return subjects


import time

BASE_URL = "https://myschool.ng"

def get_answer_and_explanation(session, answer_url):
    """
    Gets the answer and explanation from the answer page.
    NOTE: This function makes a separate request for each question, which is
    inefficient. A more advanced solution could look for an internal API,
    but for a simple scraper, this is the most straightforward approach.
    """
    try:
        time.sleep(1) # Be polite
        response = session.get(answer_url, timeout=30)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"Error fetching answer from {answer_url}: {e}")
        return None, None, None

    soup = BeautifulSoup(response.text, 'html.parser')

    # Get correct answer
    correct_answer = None
    answer_h5 = soup.find('h5', class_='text-success')
    if answer_h5 and 'Correct Answer:' in answer_h5.text:
        correct_answer = answer_h5.text.replace('Correct Answer:', '').replace('Option', '').strip()

    # Get explanation
    explanation = None
    explanation_h5 = soup.find('h5', string=lambda t: t and 'Explanation' in t)
    if explanation_h5:
        explanation_text = explanation_h5.next_sibling
        if explanation_text:
            explanation = str(explanation_text).strip()


    # Get year
    year = None
    breadcrumb_items = soup.find_all('li', class_='breadcrumb-item')
    if len(breadcrumb_items) > 3:
        year_text_tag = breadcrumb_items[3].find('a')
        if year_text_tag:
            year_text = year_text_tag.text.strip()
            # Extract year from text like "WAEC 2004"
            year = ''.join(filter(str.isdigit, year_text))


    return correct_answer, explanation, year

def scrape_questions(session, subject_url, all_questions=None, page_limit=float('inf'), current_page=1):
    """
    Scrapes questions from a subject page and follows pagination.
    """
    if all_questions is None:
        all_questions = []

    if current_page > page_limit:
        return all_questions

    try:
        print(f"Scraping {subject_url} (Page {current_page})")
        time.sleep(1)  # Be polite
        response = session.get(subject_url, timeout=30)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"Error fetching questions from {subject_url}: {e}")
        return all_questions

    soup = BeautifulSoup(response.text, 'html.parser')

    question_items = soup.find_all('div', class_='question-item')

    for item in question_items:
        question_number_tag = item.find(class_='question_sn')
        question_desc_tag = item.find(class_='question-desc')
        options_list = item.find('ul', class_='list-unstyled')
        answer_link_tag = item.find('a', class_='btn-outline-danger')

        if not all([question_number_tag, question_desc_tag, answer_link_tag]):
            continue

        question_number = question_number_tag.text.strip()
        question_desc = question_desc_tag.text.strip()

        options = {}
        if options_list:
            for option in options_list.find_all('li'):
                key_tag = option.find('strong')
                if not key_tag:
                    continue
                key = key_tag.text.strip().replace('.', '')
                value = option.text.replace(key_tag.text, '').strip()
                options[key] = value

        answer_link = answer_link_tag['href']
        if not answer_link.startswith('http'):
            answer_link = f"{BASE_URL}{answer_link}"

        correct_answer, explanation, year = get_answer_and_explanation(session, answer_link)

        question_data = {
            'number': question_number,
            'text': question_desc,
            'options': options,
            'correct_answer': correct_answer,
            'explanation': explanation,
            'year': year,
        }
        all_questions.append(question_data)

    # Handle pagination
    next_page_link = soup.select_one('li.page-item a[rel="next"]')
    if next_page_link and current_page < page_limit:
        next_page_url = next_page_link['href']
        if not next_page_url.startswith('http'):
             next_page_url = f"{BASE_URL}{next_page_url}"
        scrape_questions(session, next_page_url, all_questions, page_limit, current_page + 1)


    return all_questions

import os
import re

def sanitize_filename(filename):
    """
    Sanitizes a string to be used as a filename.
    """
    return re.sub(r'[^a-zA-Z0-9_.-]', '', filename)

import argparse

def scrape_subject(subject_name, exam_type, start_year, end_year, strict_mode=False):
    """
    Scrapes past questions for a specific subject and exam type within a given year range.
    """
    classroom_url = f"{BASE_URL}/classroom"
    output_dir = "past_questions"

    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    paper_types = ['obj', 'theory']

    with requests.Session() as session:
        # First, get the URL for the specified subject
        all_subjects = get_subjects(session, classroom_url)
        if not all_subjects or subject_name not in all_subjects:
            print(f"Error: Subject '{subject_name}' not found on the website.")
            print(f"Available subjects are: {', '.join(all_subjects.keys())}")
            return

        subject_url = all_subjects[subject_name]
        print(f"Found URL for {subject_name}: {subject_url}")

        subject_questions_by_year = {}

        for paper_type in paper_types:
            print(f"\n--- Scraping {paper_type.capitalize()} Questions for {exam_type.upper()} ---")

            if '?' not in subject_url:
                scrape_url = f"{subject_url}?exam_type={exam_type}&type={paper_type}"
            else:
                scrape_url = f"{subject_url}&exam_type={exam_type}&type={paper_type}"

            questions = scrape_questions(session, scrape_url)

            print(f"Found {len(questions)} {paper_type} questions for {subject_name}")

            for q in questions:
                year_str = q.get('year')
                if not year_str or not year_str.isdigit():
                    continue

                year = int(year_str)
                # Filter by year
                if start_year <= year <= end_year:
                    # Conditionally filter by data integrity if strict mode is on
                    if strict_mode and (not q.get('options') or not q.get('correct_answer')):
                        print(f"  [Strict Mode] Skipping question number {q.get('number')} for year {year} due to missing data.")
                        continue

                    if year not in subject_questions_by_year:
                        subject_questions_by_year[year] = []
                    subject_questions_by_year[year].append(q)

        # Save the filtered questions for the current subject to files
        for year, questions in subject_questions_by_year.items():
            sanitized_subject_name = sanitize_filename(subject_name)
            filename = f"{exam_type.upper()}_{sanitized_subject_name}_{year}.json"
            filepath = os.path.join(output_dir, filename)
            with open(filepath, 'w') as f:
                json.dump(questions, f, indent=2)
            print(f"Saved {len(questions)} questions to {filepath}")

    print("Scraping complete for this subject.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrape past exam questions from myschool.ng")
    parser.add_argument("subject_name", type=str, help="The name of the subject to scrape (e.g., 'Mathematics').")
    parser.add_argument("--exam_type", type=str, required=True, choices=['waec', 'jamb'], help="The type of exam (waec or jamb).")
    parser.add_argument("--start_year", type=int, default=2010, help="The starting year for scraping.")
    parser.add_argument("--end_year", type=int, default=2024, help="The ending year for scraping.")
    parser.add_argument("--strict", action='store_true', help="If set, only save questions that have both options and a correct answer.")

    args = parser.parse_args()

    scrape_subject(args.subject_name, args.exam_type, args.start_year, args.end_year, args.strict)
