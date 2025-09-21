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

def scrape_myschool():
    """
    Scrapes past questions from myschool.ng for all subjects and years.
    """
    classroom_url = f"{BASE_URL}/classroom"

    all_questions_data = {}
    paper_types = ['obj', 'theory'] # Add 'practical' if needed

    with requests.Session() as session:
        subjects = get_subjects(session, classroom_url)

        if subjects:
            # Limiting to 2 subjects and 2 pages for demonstration
            for subject_name, subject_url in list(subjects.items())[:2]:
                print(f"Scraping questions for: {subject_name}")

                for paper_type in paper_types:
                    print(f"\n--- Scraping {paper_type.capitalize()} Questions ---")

                    # Construct the URL for the paper type
                    if '?' not in subject_url:
                        scrape_url = f"{subject_url}?exam_type=waec&type={paper_type}"
                    else:
                        scrape_url = f"{subject_url}&exam_type=waec&type={paper_type}"

                    # Scrape a limited number of pages for demonstration
                    questions = scrape_questions(session, scrape_url, page_limit=2)

                    print(f"Found {len(questions)} {paper_type} questions for {subject_name}")

                    for q in questions:
                        year = q.get('year', 'Unknown')
                        if year not in all_questions_data:
                            all_questions_data[year] = {}

                        if subject_name not in all_questions_data[year]:
                            all_questions_data[year][subject_name] = {}

                        if paper_type not in all_questions_data[year][subject_name]:
                            all_questions_data[year][subject_name][paper_type] = []

                        all_questions_data[year][subject_name][paper_type].append(q)

    # Re-structure the data for the final JSON output
    final_data = {"WAEC": []}
    for year, subjects in all_questions_data.items():
        year_data = {"year": year, "subjects": []}
        for subject_name, papers in subjects.items():
            subject_data = {"name": subject_name, "papers": []}
            for paper_name, questions in papers.items():
                paper_data = {"name": paper_name, "questions": questions}
                subject_data["papers"].append(paper_data)
            year_data["subjects"].append(subject_data)
        final_data["WAEC"].append(year_data)

    with open('past_questions.json', 'w') as f:
        json.dump(final_data, f, indent=2)

    print("Scraping complete. Data saved to past_questions.json")

if __name__ == "__main__":
    scrape_myschool()
