CREATE USER 'lab_reader'@'%' IDENTIFIED BY 'lab_reader_test_password';
CREATE USER 'lab_operator'@'%' IDENTIFIED BY 'lab_operator_test_password';
GRANT SELECT ON access_lab.* TO 'lab_reader'@'%';
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, DROP, INDEX ON access_lab.* TO 'lab_operator'@'%';
CREATE TABLE access_lab.items (id INT PRIMARY KEY, name VARCHAR(50), amount DECIMAL(16,2));
INSERT INTO access_lab.items VALUES (1, 'Alpha', 12.50), (2, 'Beta', 23.75);
CREATE DATABASE restricted_lab;
CREATE TABLE restricted_lab.secrets (value VARCHAR(30));
INSERT INTO restricted_lab.secrets VALUES ('not_authorized');
