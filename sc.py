import os
import re
import sys
import sqlite3
import requests
import argparse
from flask import Flask, Response, jsonify, request

global storage_method, connection, db_name
app = Flask("ECM3408 Flask Server")


class CellNotFoundError(Exception):
    pass


def validate_cell_input(cell):
    return re.match(r"[A-Z]+[0-9]+$", cell)


def check_parenthesis(formula):
    count = 0
    for character in formula:
        if character == '(':
            count += 1
        elif character == ')':
            count -= 1
            if count < 0:
                # Too many close brackets
                return False

    if count > 0:
        # Too many open brackets
        return False

    return True


@app.route('/cells/<cell>', methods=['PUT'])
def update_cell(cell):
    data = request.json

    def validate_cell_formula_input(url_cell, input_json):
        # the cell must be valid
        # the cell in JSON must be the same as the cell in the URL
        # both 'id' and 'formula' should be valid keys in 'input_json'
        # the formula must not be blank and must only contain valid characters and parenthesis
        try:
            valid_chars = True
            allowed_chars = r'[A-Z0-9\*\+\-\/\(\)\ ]'
            for char in input_json['formula']:
                if not re.match(allowed_chars, char):
                    # The character is not a valid character
                    valid_chars = False

            # Return the 'AND' of all conditions
            return (validate_cell_input(input_json['id']) and
                    url_cell == input_json['id'] and
                    len(input_json['formula']) > 0 and
                    check_parenthesis(input_json['formula']) and
                    valid_chars)
        except KeyError:
            # If there is a key error, then 'formula' and/or 'id' are not present in input_json
            return False

    if not validate_cell_formula_input(cell, data):
        # if the cell or forumla is not valid, return code 400
        return Response(status=400)

    if storage_method == 'sqlite':
        cursor = connection.cursor()

        if len(cursor.execute("SELECT * FROM SPREADSHEET WHERE id=?", (data['id'],)).fetchall()) == 0:
            # the cell does not already exist, so insert the cell id and formula into the spreadsheet and return 201
            cursor.execute("INSERT INTO SPREADSHEET VALUES (?, ?)", (data['id'], data['formula']))
            connection.commit()
            return Response(status=201)
        else:
            # the cell already exists, so change the contents of its formula and return 204
            cursor.execute('UPDATE SPREADSHEET SET formula=? WHERE id=?', (data['formula'], data['id']))
            connection.commit()
            return Response(status=204)

    elif storage_method == 'firebase':
        # Return code 201 unless the cell alredy exists, in this case, return 204
        return_code = 201
        if cell in get_cells():
            return_code = 204

        # add the cell or change the cell contents using a request
        response = requests.put(
            url="https://" + db_name + "-default-rtdb.europe-west1.firebasedatabase.app/cells/" + data['id'] + ".json",
            json={"formula": data['formula']})

        if response.status_code == 200:
            # return (201 or 204) as long as the return code from firebase is 200
            return Response(status=return_code)

    # The program should not reach this point, if it does, return code 500 (Internal Server Error)
    return Response(status=500)


def evaluate_formula(formula):
    def evaluate_cell(match):
        # get the CELL from the match object and recurse into 'evaluate_formula' to evaluate its value
        cell = match.string[match.span()[0]:match.span()[1]]
        return str(evaluate_formula(cell))

    if validate_cell_input(formula):
        # the formula is a single cell, so get the cell value from the database and return it
        cell_contents = read_cell(formula)
        # read the cell using the 'read_cell' function
        if cell_contents.status_code == 200:
            # if this returns 200, then return the formula
            return cell_contents.json['formula']
        else:
            # if the return code is not 200, then raise a custom error return 0 (as this will be placed into a formula)
            return 0

    # Replace cell indexes with their values and return the evaluated expression
    clean_formula = re.compile(r'[A-Z]+[0-9]+').sub(evaluate_cell, formula)
    return str(eval(clean_formula))


@app.route('/cells/<cell>', methods=['GET'])
def read_cell(cell):
    if not validate_cell_input(cell):
        # return code 404 (Not Found)
        return Response(status=404)

    try:
        if storage_method == 'sqlite':
            cursor = connection.cursor()

            query = cursor.execute('SELECT * FROM SPREADSHEET WHERE id=?', (cell,)).fetchall()
            if len(query) == 0:
                # if the length of the query result is 0 then the cell does not exist, return 404
                return Response(status=404)
            else:
                # if the formula is a single cell, then retrieve the cell and return its contents, OR return a 404 if the cell does not exist
                if validate_cell_input(query[0][1]):
                    # the formula is a single cell, so get the cell value from the database and return it
                    cell_contents = read_cell(query[0][1])

                    if cell_contents.status_code == 200:
                        # if this returns 200, then return the formula
                        return cell_contents.json['formula']
                    else:
                        # if the return code is not 200, then return 404
                        return Response(status=404)
                else:
                    # the cell contains a formula and therefore the formula must be evaulated
                    # return the id and formula in JSON
                    # This returns code 200 (OK) by default
                    return jsonify(id=query[0][0], formula=evaluate_formula(query[0][1]))


        elif storage_method == 'firebase':

            response = requests.get(
                url="https://" + db_name + "-default-rtdb.europe-west1.firebasedatabase.app/cells/" + cell + ".json")
            if response.status_code == 200:
                if response.json() == None:
                    return Response(status=404)
                else:
                    # cell exists and json contains the formula
                    # This returns code 200 (OK) by default
                    return jsonify(id=cell, formula=evaluate_formula(response.json()['formula']))

    except CellNotFoundError:
        # A cell which was contained within a formula did not exist, therefore return 404 (Not Found)
        return Response(status=404)

    # the program should not reach this point, if it does, return code 500 (Internal Server Error)
    return Response(status=500)


@app.route('/cells/<cell>', methods=['DELETE'])
def delete_cell(cell):
    if not validate_cell_input(cell):
        # return code 404 (Not Found)
        return Response(status=404)

    if storage_method == 'sqlite':
        cursor = connection.cursor()

        if len(cursor.execute("SELECT * FROM SPREADSHEET WHERE id=?", (cell,)).fetchall()) == 0:
            # the cell does not already exist, return 404
            return Response(status=404)
        else:
            # the cell already exists, so delete it and return 204
            cursor.execute("DELETE FROM SPREADSHEET WHERE id=?", (cell,))
            connection.commit()
            return Response(status=204)
    elif storage_method == 'firebase':

        response = requests.delete(
            url="https://" + db_name + "-default-rtdb.europe-west1.firebasedatabase.app/cells/" + cell + ".json")
        if response.status_code == 200:
            return Response(status=204)

    # the program should not reach this point, if it does, return code 500 (Internal Server Error)
    return Response(status=500)


def get_cells():
    if storage_method == 'sqlite':
        # get all cells id's from the database and write them to the cells array
        cells = []
        cursor = connection.cursor()
        for cell in cursor.execute("SELECT id FROM SPREADSHEET ").fetchall():
            cells.append(cell[0])
        return cells

    elif storage_method == 'firebase':
        # get all cells from firebase and return them as an array
        try:
            response = requests.get(
                url="https://" + db_name + "-default-rtdb.europe-west1.firebasedatabase.app/cells.json")
            return list(response.json().keys())
        except AttributeError:
            return []

    return []

@app.route('/cells', methods=['GET'])
def list_cells():
    try:
        # return an array containing the cells that are in the database
        # This returns code 200 (OK) by default
        return jsonify(get_cells())
    except Exception:
        # if we get here, there was an issue retrieving cells, return 500
        return Response(status=500)


def initial_cleanup():
    # if the database has already been created, delete it
    if os.path.isfile('./mydb.db'):
        os.remove("./mydb.db")


def setup_db():
    # create a database and set up a table and headers
    connection = sqlite3.connect("./mydb.db", check_same_thread=False)
    cursor = connection.cursor()
    cursor.execute(""" CREATE TABLE SPREADSHEET (
                                id VARCHAR(255) NOT NULL,
                                formula VARCHAR(225) NOT NULL
                        );""")
    connection.commit()
    # return the connection to be used by the rest of the program
    return connection


def clear_firebase():
    # clear each cell that exists in firebase
    for cell in get_cells():
        delete_cell(cell)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument("-r")
    args = parser.parse_args()
    storage_method = args.r
    print("Started Program")

    if storage_method == 'sqlite':
        print("Using Storage Method: " + storage_method + '\n')
        initial_cleanup()
        connection = setup_db()
    elif storage_method == 'firebase':
        try:
            db_name = os.environ['FBASE']
            db_url = "https://" + db_name + "-default-rtdb.europe-west1.firebasedatabase.app/cells"
            #clear_firebase() # REMEMBER TO UNHASH THIS BEFORE HANDING IN
            print("Using Storage Method: " + storage_method)
            print("Firebase Database Name: " + db_name)
            print("Firebase Database URL: " + db_url + '\n')
        except KeyError:
            print("ERROR: ENV VAR 'FBASE' DOES NOT EXIST")
            quit()
    else:
        print("INCORRECT STORAGE METHOD GIVEN - ACCEPTS 'sqlite' or 'firebase' ")
        quit()

    app.run(host='localhost', port=3000)
